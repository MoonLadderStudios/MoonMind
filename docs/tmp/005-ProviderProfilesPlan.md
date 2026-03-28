# Provider Profiles Implementation Plan

Status: Draft  
Owners: MoonMind Engineering  
Last Updated: 2026-03-28

## 1. Purpose

This document defines the remaining work needed to move MoonMind from its current partial transition state into a fully functional **Provider Profiles** system aligned with:

- `docs/Security/ProviderProfiles.md`
- `docs/Security/SecretsSystem.md`
- `docs/ManagedAgents/OAuthTerminal.md`

This is a phased implementation plan, but each phase should land as a coherent slice. MoonMind is still pre-release, so the project should avoid long-lived dual systems where possible. In particular, avoid preserving both “Auth Profile” and “Provider Profile” semantics longer than needed inside any single subsystem.

---

## 2. Current State Snapshot

MoonMind already has meaningful pieces of the Provider Profile transition in place, but they are not yet aligned end-to-end.

### 2.1 Already present

- A `managed_agent_provider_profiles` database model exists.
- Provider-aware fields already exist in the DB model, including `provider_id`, `provider_label`, `credential_source`, `runtime_materialization_mode`, `secret_refs`, `clear_env_keys`, `env_template`, `file_templates`, `home_path_overrides`, `command_behavior`, and profile-level slot/cooldown controls.
- `ProfileSelector` already exists on `AgentExecutionRequest` with provider-aware selector fields.
- The manager workflow already supports provider-aware selection concepts such as `providerId`, `tagsAny`, `tagsAll`, and `runtimeMaterializationMode`.
- Crash-recovery slot lease persistence already exists.
- A `ManagedSecret` model exists and provides an encrypted durable secret store foundation.
- An OAuth session workflow and activity set already exist.

### 2.2 Major gaps and inconsistencies

- The runtime contracts are still mixed between old Auth Profile concepts and new Provider Profile concepts.
- The manager workflow, service layer, activity names, workflow name, workflow IDs, comments, and file names still use `AuthProfile` naming in many places.
- `AgentExecutionRequest` still requires `executionProfileRef` instead of cleanly supporting optional exact-profile selection plus selector-based routing.
- `ManagedAgentAdapter` is still driven primarily by old `auth_mode` / `api_key_ref` style logic and does not yet implement the updated Provider Profile materialization model.
- `ManagedRuntimeProfile` still reflects the old launch contract and cannot faithfully carry the updated Provider Profile design.
- OAuth sessions are intentionally nonfunctional today because the legacy auth runner transport was removed before the replacement PTY/WebSocket flow was implemented.
- The OAuth session DB model and workflow still contain transport-era fields and states that no longer match the desired first-party browser terminal design.
- OAuth profile registration still points at old auth-profile concepts rather than the new Provider Profile contract.
- The default-model portion of the updated Provider Profile design is not yet represented consistently across DB, API, runtime schema, launcher logic, and runtime strategies.
- Secret references exist in the model, but the end-to-end SecretRef-aware materialization pipeline is not yet complete.
- Mission Control still needs first-class Provider Profile management, OAuth terminal UX, validation, and visibility.

### 2.3 Implication for planning

The remaining work is not just “finish the UI.” The transition still requires coordinated work across:

- persistence and migrations,
- Pydantic/API contracts,
- Temporal workflow/activity naming and payloads,
- launch-time materialization,
- secret resolution boundaries,
- OAuth session transport,
- Mission Control UX,
- tests and cleanup.

---

## 3. Target End State

MoonMind should end this plan with all of the following true:

1. Provider Profiles are the only managed-runtime profile concept in code, docs, and UI.
2. The durable Provider Profile contract matches `docs/Security/ProviderProfiles.md`.
3. Secret references and launch-time secret resolution match `docs/Security/SecretsSystem.md`.
4. OAuth-backed Provider Profiles work through the `OAuthTerminal.md` browser terminal architecture.
5. Managed runtime launches are driven by provider-aware materialization rather than old auth-mode branches.
6. Manager workflow naming, signals, APIs, and observability all use Provider Profile terminology.
7. Mission Control can create, edit, validate, enable/disable, inspect, and use Provider Profiles.
8. End-to-end tests cover Provider Profile selection, cooldown, secret-backed launch, and OAuth-backed profile registration.

---

## 4. Implementation Principles

### 4.1 Coherent phases, not half-migrations

Each phase should leave the touched subsystem internally consistent.

Examples:

- If the workflow name changes from `AuthProfileManager` to `ProviderProfileManager`, the service layer, task IDs, comments, tests, and observability around that subsystem should be updated in the same phase.
- If the runtime launch schema changes, the launcher and adapter must be updated in the same phase.

### 4.2 No raw secrets in durable contracts

No phase may introduce raw secrets into:

- workflow payloads,
- run metadata,
- profile rows,
- artifacts,
- logs,
- test fixtures committed to the repository.

### 4.3 Preserve working execution wherever practical

Managed non-OAuth execution should remain functional while the migration progresses.

### 4.4 Restore OAuth only after the new transport exists

Do not revive a tmate-like bridge or reintroduce the removed browser runner path. The only acceptable path forward is the first-party PTY/WebSocket architecture.

---

## 5. Phase Overview

- **Phase 1 — Contract and naming alignment**
- **Phase 2 — Persistence and service layer completion**
- **Phase 3 — Provider Profile manager and selection path completion**
- **Phase 4 — Runtime materialization and secret-resolution integration**
- **Phase 5 — OAuth terminal and OAuth-backed profile completion**
- **Phase 6 — Mission Control UX and operator workflows**
- **Phase 7 — Cleanup, migration finish, and hardening**

---

## 6. Phase 1 — Contract and Naming Alignment

### 6.1 Goal

Make the codebase consistently talk about **Provider Profiles** rather than a mixed Provider/Auth model, and align the runtime contracts with the updated design.

### 6.2 Why this phase is needed

The project already has a provider-profile DB model, but much of the execution path still uses `AuthProfile` naming and older payload shapes. This creates confusion and makes later phases harder.

### 6.3 Tasks

#### A. Rename code symbols and files

- [ ] Rename `MoonMind.AuthProfileManager` to `MoonMind.ProviderProfileManager`.
- [ ] Rename the manager workflow file/class to use `provider_profile_manager` naming.
- [ ] Rename `auth_profile_service.py` to `provider_profile_service.py`.
- [ ] Rename helper functions such as `sync_auth_profile_manager(...)` to `sync_provider_profile_manager(...)`.
- [ ] Rename internal comments, logger messages, and docstrings that still say “auth profile.”
- [ ] Rename Temporal workflow IDs from `auth-profile-manager:<runtime_id>` to `provider-profile-manager:<runtime_id>`.
- [ ] Rename any activity names that are still `auth_profile.*` to `provider_profile.*`.
- [ ] Rename any routing/activity-catalog labels that still expose the old prefix.

#### B. Align Pydantic/runtime contracts

- [ ] Replace the legacy `ManagedAgentAuthProfile` runtime schema with a `ManagedAgentProviderProfile` runtime schema.
- [ ] Update `ManagedRuntimeProfile` so it can represent the new launch contract, including:
  - `provider_id`
  - `provider_label`
  - `credential_source`
  - `runtime_materialization_mode`
  - `default_model`
  - `model_overrides`
  - `secret_refs` as structured SecretRef-like values rather than `dict[str, str]`
  - `env_template`
  - `file_templates`
  - `home_path_overrides`
  - `command_behavior`
- [ ] Remove old launch-shaping fields that no longer represent the desired model, or demote them to transitional/internal fields.
- [ ] Change `AgentExecutionRequest.execution_profile_ref` from required string to optional exact-profile reference.
- [ ] Preserve selector-based routing as the preferred alternative when no exact profile is supplied.
- [ ] Remove the need for sentinel values such as `"auto"` in the long-term contract.
- [ ] Normalize selector field names so the manager, workflow, UI, and API all agree on one contract shape.

#### C. Align enums and workflow catalog models

- [ ] Update `TemporalWorkflowType` to reflect the Provider Profile manager name.
- [ ] Audit any dashboard filters, projections, or labels that refer to `AUTH_PROFILE_MANAGER`.
- [ ] Update constants, search attributes, and workflow-type strings used for observability.

### 6.4 Tests

- [ ] Unit tests for the updated Pydantic schemas.
- [ ] Tests covering optional exact-profile reference + selector-only requests.
- [ ] Tests ensuring no raw secret-like values can be accepted into runtime contracts.

### 6.5 Exit criteria

This phase is done when the codebase no longer presents “Auth Profile” as the primary runtime profile concept, and the main request/profile contracts match the updated Provider Profile terminology and shape.

---

## 7. Phase 2 — Persistence and Service Layer Completion

### 7.1 Goal

Finish the persistence model so the database, service layer, and registration paths fully support the updated Provider Profile design.

### 7.2 Why this phase is needed

The DB model already exists, but it does not yet fully represent the updated design, and parts of the service/registration path still point at old auth-profile concepts.

### 7.3 Tasks

#### A. Complete the Provider Profile table shape

- [ ] Add missing fields required by the updated design:
  - `default_model`
  - `model_overrides`
- [ ] Review JSON/JSONB usage and ensure the table defaults are consistent and safe.
- [ ] Ensure indexes cover expected runtime/provider lookups.
- [ ] Review whether `owner_user_id` should be a proper FK or remain loosely typed; make a deliberate choice.

#### B. Complete Provider Profile CRUD/service behavior

- [ ] Implement or finish the Provider Profile repository/service layer.
- [ ] Ensure create/update/list/get/delete/enable/disable operations operate on `ManagedAgentProviderProfile` only.
- [ ] Validate provider-profile inputs before persistence.
- [ ] Add validation for `secret_refs`, `env_template`, `file_templates`, `clear_env_keys`, and `command_behavior`.
- [ ] Enforce that raw secret values are rejected at the service boundary.

#### C. Fix registration paths that still use old models

- [ ] Update OAuth profile registration to create/update `ManagedAgentProviderProfile`, not old auth-profile classes.
- [ ] Remove imports or references to old non-provider profile classes that no longer exist or should no longer exist.
- [ ] Ensure Provider Profile manager sync uses the Provider Profile service consistently.

#### D. Create/finish migration scripts

- [ ] Add Alembic migrations for any missing Provider Profile columns.
- [ ] Add migrations needed by renamed enums or renamed workflow types.
- [ ] Add data migration logic for any remaining local-dev rows still shaped like Auth Profiles.
- [ ] Keep migrations one-way and clean rather than introducing long-lived compatibility aliases.

### 7.4 Tests

- [ ] DB migration tests.
- [ ] Service-layer validation tests.
- [ ] CRUD tests for Provider Profile persistence.
- [ ] Tests proving raw secret values cannot be stored in Provider Profile rows.

### 7.5 Exit criteria

This phase is done when the persistence and service layers can fully store and retrieve the updated Provider Profile shape, and no registration path still writes old auth-profile records.

---

## 8. Phase 3 — Provider Profile Manager and Selection Path Completion

### 8.1 Goal

Make profile selection, leasing, cooldown, and routing fully align with Provider Profile semantics.

### 8.2 Why this phase is needed

The manager is already partly provider-aware, but it still uses old naming and still sits in an execution path that depends on legacy assumptions such as required `executionProfileRef` and auth-profile activity prefixes.

### 8.3 Tasks

#### A. Finish manager rename and orchestration wiring

- [ ] Rename the workflow implementation from AuthProfileManager to ProviderProfileManager.
- [ ] Update all callers to use the new workflow name and workflow ID convention.
- [ ] Update manager-start/reset/ensure/sync activities to Provider Profile naming.
- [ ] Update AgentRun logic to signal the renamed manager.
- [ ] Update any task queue, activity catalog, or worker-registration references.

#### B. Align request semantics with the new design

- [ ] Support exact-profile resolution when `execution_profile_ref` is present.
- [ ] Support selector-based resolution when `execution_profile_ref` is absent.
- [ ] Preserve deterministic behavior for replay while updating the runtime contract.
- [ ] Remove or deprecate the old `"auto"` profile sentinel.

#### C. Tighten selector behavior

- [ ] Ensure the manager supports the final selector contract:
  - `provider_id`
  - `tags_any`
  - `tags_all`
  - `runtime_materialization_mode`
- [ ] Normalize selector field names at one boundary only.
- [ ] Ensure tie-breaking follows the updated design:
  - highest priority first
  - then most available slots
- [ ] Ensure waiting reasons shown to the UI are provider-aware and operator-friendly.

#### D. Verify cooldown and slot persistence behavior

- [ ] Keep crash-recovery lease persistence intact after the rename.
- [ ] Verify lease restoration still works with the new workflow name and service names.
- [ ] Ensure 429 cooldown behavior preserves provider intent when a run re-queues.
- [ ] Ensure a run cannot silently fall through to an unintended provider unless the selector/priority rules explicitly allow it.

#### E. Profile snapshot behavior

- [ ] Update AgentRun’s profile snapshot handling so cooldown and retry behavior use the final Provider Profile contract.
- [ ] Remove leftover assumptions that a profile is basically just `auth_mode + api_key_ref + volume_ref`.

### 8.4 Tests

- [ ] Manager unit tests for exact-profile selection.
- [ ] Manager unit tests for selector-based selection.
- [ ] Manager unit tests for provider-aware fallback rules.
- [ ] Cooldown tests.
- [ ] Lease persistence and crash-recovery tests.
- [ ] Tests proving disabled profiles are excluded from assignment.

### 8.5 Exit criteria

This phase is done when managed runs acquire slots through a fully Provider Profile-aligned manager path and selection behavior matches the updated design.

---

## 9. Phase 4 — Runtime Materialization and Secret-Resolution Integration

### 9.1 Goal

Replace the old auth-mode launch shaping with a Provider Profile materialization pipeline that works with the Secrets System.

### 9.2 Why this phase is needed

This is the most important technical phase. Today, the adapter and env-shaping path still revolve around `auth_mode`, `api_key_ref`, and older environment-shaping helpers. The updated design requires profile-driven materialization based on credential source class, SecretRef bindings, environment templates, generated files, and runtime strategy hooks.

### 9.3 Tasks

#### A. Build a shared Provider Profile materializer

- [ ] Implement a reusable materialization pipeline that performs the required order:
  1. base environment
  2. runtime defaults
  3. `clear_env_keys`
  4. secret resolution for launch-only use
  5. `file_templates`
  6. `env_template`
  7. `home_path_overrides`
  8. runtime strategy shaping
  9. command construction
- [ ] Make the pipeline layer onto a base environment rather than replace it.
- [ ] Ensure materialization is runtime-scoped and launch-only.

#### B. Integrate SecretRef-aware resolution

- [ ] Define the runtime-facing SecretRef representation used by launcher/materializer code.
- [ ] Implement a launch-time secret resolution service boundary for `secret_ref` profiles.
- [ ] Integrate `ManagedSecret`-backed resolution for local-first `db_encrypted` secrets.
- [ ] Ensure the materializer never writes resolved plaintext secrets into workflow payloads or durable rows.
- [ ] Ensure logs and diagnostics redact resolved secret values.

#### C. Replace old adapter logic

- [ ] Rewrite `ManagedAgentAdapter.start()` so it no longer branches primarily on old `auth_mode` logic.
- [ ] Stop relying on `api_key_ref` / `runtime_env_overrides` as the main profile contract.
- [ ] Resolve and apply `credential_source` and `runtime_materialization_mode` instead.
- [ ] Use `default_model` / `model_overrides` from the Provider Profile contract.
- [ ] Preserve proxy-first behavior where explicitly supported.
- [ ] Ensure `secret_refs`, `env_template`, and `file_templates` are actually honored in launch preparation.

#### D. Update launch-time runtime schema and launcher

- [ ] Update `ManagedRuntimeProfile` to carry the final materialization inputs.
- [ ] Update the managed runtime launch activity/launcher to understand the new schema.
- [ ] Add support for sensitive generated runtime files that are ephemeral by default.
- [ ] Add cleanup for temporary runtime files when runs finish or fail.

#### E. Update runtime strategies

- [ ] Gemini runtime strategy:
  - OAuth home support
  - API-key path where applicable
  - conflicting-key clearing
- [ ] Claude Code runtime strategy:
  - Anthropic OAuth
  - Anthropic API key
  - Anthropic-compatible env bundles such as MiniMax and Z.AI
  - model flag suppression when env-driven model defaults exist
- [ ] Codex CLI runtime strategy:
  - OAuth home
  - OpenAI/provider API-key paths where supported
  - config-bundle and composite profile materialization
  - named provider profile selection in config
- [ ] Ensure strategies consume `command_behavior` rather than hard-coded branching where possible.

#### F. Redaction and artifact safety

- [ ] Ensure generated config files with secrets are not durably published as artifacts by default.
- [ ] Add tests for redaction of launch-time values in logs and diagnostics.

### 9.4 Tests

- [ ] Materializer unit tests per materialization mode.
- [ ] Secret resolution tests.
- [ ] Redaction tests.
- [ ] Managed launch integration tests for:
  - Gemini OAuth profile
  - Claude Anthropic API-key profile
  - Claude MiniMax env-bundle profile
  - Codex MiniMax composite profile

### 9.5 Exit criteria

This phase is done when managed runtime launches are actually driven by the new Provider Profile materialization model, not by the legacy auth-mode contract.

---

## 10. Phase 5 — OAuth Terminal and OAuth-Backed Provider Profile Completion

### 10.1 Goal

Restore fully working OAuth-backed Provider Profiles through the first-party terminal architecture defined in `OAuthTerminal.md`.

### 10.2 Why this phase is needed

Today, OAuth sessions are structurally present but intentionally broken. The workflow still calls a removed `start_auth_runner` path, and the data model still reflects old transport assumptions.

### 10.3 Tasks

#### A. Replace the dead OAuth launch path

- [ ] Remove the assumption that `oauth_session.start_auth_runner` returns browser URLs.
- [ ] Implement the PTY/WebSocket bridge startup activity path.
- [ ] Implement short-lived auth container startup compatible with the mounted auth volume model.
- [ ] Implement terminal-bridge teardown activity path.

#### B. Update OAuth session workflow

- [ ] Replace `oauth_runner_ready` with `bridge_ready`.
- [ ] Update status transitions to the transport-neutral lifecycle in `OAuthTerminal.md`.
- [ ] Add signals/updates as needed for terminal connected/disconnected and finalize/cancel flows.
- [ ] Preserve durable timeout and cancellation handling.

#### C. Update OAuth session persistence model

- [ ] Remove old transport-era fields from the main OAuth session model where appropriate:
  - `oauth_web_url`
  - `oauth_ssh_url`
- [ ] Add fields needed for the new architecture:
  - `terminal_session_id`
  - `terminal_bridge_id`
  - `connected_at`
  - `disconnected_at`
  - transport-neutral metadata
- [ ] Add `oauth_terminal_sessions` table if adopting the clean split recommended by the design.
- [ ] Update migrations accordingly.

#### D. Update registration to Provider Profiles

- [ ] Make OAuth session finalization create/update `ManagedAgentProviderProfile`.
- [ ] Ensure registered OAuth-backed profiles include correct fields:
  - `provider_id`
  - `credential_source = oauth_volume`
  - `runtime_materialization_mode = oauth_home`
  - volume metadata
  - account label
  - policy defaults
- [ ] Ensure no terminal transport details are written into Provider Profile rows.

#### E. Security and session control

- [ ] Implement session-scoped terminal attach authorization.
- [ ] Add TTL enforcement and idle expiry.
- [ ] Ensure browser clients never receive credential contents directly.
- [ ] Add audit logging for OAuth session start, attach, verify, success, cancel, and failure.

#### F. Provider-specific verification

- [ ] Finish provider-specific volume verification for Gemini, Claude, Codex, and future runtimes.
- [ ] Ensure the OAuth workflow only registers the profile after verification succeeds.

### 10.4 Tests

- [ ] OAuth session workflow tests.
- [ ] PTY bridge tests.
- [ ] Volume verification tests.
- [ ] End-to-end OAuth-backed Provider Profile registration tests.
- [ ] Permission and expiry tests for terminal attach.

### 10.5 Exit criteria

This phase is done when an operator can launch an OAuth session from Mission Control, complete terminal-based login, and end up with a working OAuth-backed Provider Profile.

---

## 11. Phase 6 — Mission Control UX and Operator Workflows

### 11.1 Goal

Make Provider Profiles a first-class operator-visible capability in Mission Control.

### 11.2 Why this phase is needed

The backend can only be considered complete when users can actually manage and use Provider Profiles without hand-editing rows or relying on hidden implementation details.

### 11.3 Tasks

#### A. Provider Profiles management UI

- [ ] Build or finish a Provider Profiles page/tab/section in Mission Control.
- [ ] Support create/edit/inspect/enable/disable flows.
- [ ] Show runtime, provider, account label, credential source class, materialization mode, and effective policy.
- [ ] Show tags, priority, max parallel runs, cooldown, and status.
- [ ] Show validation state and configuration errors.

#### B. Secret binding UX

- [ ] Add UI flows for selecting or binding SecretRefs to Provider Profiles.
- [ ] Integrate with the managed secrets system.
- [ ] Show presence/health of a bound secret without revealing the secret value.

#### C. OAuth session UX

- [ ] Add “Connect with OAuth” actions for supported runtimes/providers.
- [ ] Add embedded terminal modal/panel using the OAuth terminal transport.
- [ ] Show session state, expiry, terminal connected/disconnected state, and finalize/cancel actions.
- [ ] Show resulting Provider Profile after success.

#### D. Task/run creation UX

- [ ] Allow selecting an exact Provider Profile.
- [ ] Allow selector-based routing by provider/tags/materialization mode where appropriate.
- [ ] Show which Provider Profile a run actually used.
- [ ] Surface waiting states such as “waiting for provider profile slot.”

#### E. Observability UX

- [ ] Show cooldown state and slot usage for each Provider Profile.
- [ ] Show current leases and queueing reasons where useful.
- [ ] Show recent failures related to secret resolution, profile validation, or OAuth verification.

### 11.4 Tests

- [ ] UI tests for Provider Profile CRUD.
- [ ] UI tests for OAuth terminal flows.
- [ ] UI tests for selection/launch flows.

### 11.5 Exit criteria

This phase is done when Provider Profiles are manageable and understandable from Mission Control without requiring repo knowledge.

---

## 12. Phase 7 — Cleanup, Migration Finish, and Hardening

### 12.1 Goal

Remove transitional leftovers, close correctness gaps, and harden the final system.

### 12.2 Tasks

#### A. Remove transitional code and dead paths

- [ ] Remove old `AuthProfile`-named leftovers that remain after the earlier phases.
- [ ] Remove dead OAuth URL/session transport logic.
- [ ] Remove obsolete `auth_mode`-centric helpers once the materializer supersedes them.
- [ ] Remove fallback code that only existed to bridge the transition.

#### B. Final documentation pass

- [ ] Update all doc references from Auth Profile to Provider Profile.
- [ ] Ensure docs reference `ProviderProfilesPlan.md` rather than old tmp/remaining-work docs.
- [ ] Update any onboarding or AGENTS guidance related to managed runtime credentials.

#### C. Hardening and regression coverage

- [ ] Add end-to-end tests for the most important combinations:
  - Gemini + Google OAuth
  - Claude + Anthropic OAuth
  - Claude + Anthropic API key
  - Claude + MiniMax env bundle
  - Codex + OpenAI OAuth
  - Codex + MiniMax composite profile
- [ ] Add regression tests for crash recovery of slot leases.
- [ ] Add regression tests for 429 cooldown requeue behavior.
- [ ] Add regression tests for SecretRef rotation affecting new launches but not mutating durable payloads.
- [ ] Add artifact/log redaction regression tests.

#### D. Operational polish

- [ ] Add health checks or diagnostics for Provider Profile manager workflows.
- [ ] Add validation commands or API endpoints for “test this Provider Profile.”
- [ ] Add operator-facing troubleshooting guidance for broken secret refs, failed OAuth verification, and provider cooldowns.

### 12.3 Exit criteria

This phase is done when the codebase no longer depends on transitional naming or dead paths and the final Provider Profile system is stable, test-covered, and operator-friendly.

---

## 13. Recommended Delivery Order Inside the Repo

The phases above are the intended implementation order. Within them, the most leverage comes from this sequence:

1. **Rename and contract cleanup first** so the rest of the work is easier to reason about.
2. **Persistence/service correctness second** so later workflow and UI work has a stable source of truth.
3. **Manager/selection alignment third** so routing semantics are correct early.
4. **Materialization and secret resolution fourth** because this is the core technical dependency for real provider-aware launching.
5. **OAuth terminal fifth** because it depends on the final Provider Profile and secret boundaries.
6. **UI sixth** once the backend semantics are stable.
7. **Cleanup/hardening last** after the system is actually coherent.

---

## 14. High-Risk Areas

### 14.1 Runtime launch compatibility risk

Changing the launch contract is the riskiest part of the transition because it touches adapters, activities, runtime strategies, and secrets.

Mitigation:

- land materializer tests before deleting old logic,
- keep runtime strategy changes explicit and isolated,
- verify one runtime/provider combination at a time.

### 14.2 OAuth session transport risk

The OAuth subsystem is currently present but broken. The replacement architecture spans API, Temporal, container lifecycle, PTY handling, and UI.

Mitigation:

- implement terminal bridge as a narrow subsystem,
- avoid scope creep into general live terminal access,
- finish verification/registration before polishing UX.

### 14.3 Secret-handling risk

The migration introduces more flexible SecretRef usage, which increases the chance of accidental secret leakage through logs, payloads, or generated files.

Mitigation:

- add redaction tests early,
- centralize resolution and materialization,
- review logging around every boundary that touches resolved secrets.

---

## 15. Definition of Done

The Provider Profiles transition is complete when all of the following are true:

- Provider Profiles are the only managed-runtime profile abstraction across code, docs, UI, and workflows.
- Managed launches use provider-aware materialization rather than the old auth-mode contract.
- SecretRefs are resolved only at controlled execution boundaries.
- OAuth-backed profiles work through the first-party browser terminal architecture.
- Mission Control supports full Provider Profile lifecycle management.
- Slot assignment, cooldown, and selection are provider-aware and test-covered.
- No dead auth-profile or legacy OAuth transport paths remain in the runtime-critical codepaths.

---

## 16. Immediate Next Actions

The next implementation work should start with the following concrete items:

- [ ] Create the Phase 1 rename/contract PR.
- [ ] Add DB/model changes for `default_model` and `model_overrides`.
- [ ] Update runtime schemas to remove the forced `executionProfileRef` requirement.
- [ ] Rename the manager workflow/service/activity surface from AuthProfile to ProviderProfile.
- [ ] Open a dedicated implementation track for the Provider Profile materializer and SecretRef resolution boundary.
- [ ] Open a dedicated implementation track for OAuth terminal bridge + session model rewrite.

