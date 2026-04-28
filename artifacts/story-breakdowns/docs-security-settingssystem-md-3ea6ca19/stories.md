# Story Breakdown: Settings System

Source design: `docs/Security/SettingsSystem.md`
Original source document reference path: `docs/Security/SettingsSystem.md`
Story extraction date: 2026-04-28T04:33:46Z
Requested output mode: jira

## Design Summary

The Settings System design defines Mission Control as a unified, backend-driven configuration plane for provider profiles, managed secrets, user/workspace settings, operational controls, and diagnostics. Its core contract is a declarative settings catalog plus scoped overrides, effective-value resolution, server-side validation, auditability, and SecretRef-only handling for sensitive values. The implementation boundary is explicit: Settings may expose adjacent subsystem workflows, but Secrets, Provider Profiles, Operations, and runtime strategies remain authoritative for their own semantics. Security, durability, observability, migration, and test requirements are first-class design constraints rather than optional follow-up work.

## Coverage Points

- `DESIGN-REQ-001` (requirement) Unified Mission Control settings surface - Mission Control must expose Providers & Secrets, User / Workspace, and Operations under one stable Settings page instead of one-off forms. Source: 1. Summary; 3. Goals; 6. Settings Page Topology.
- `DESIGN-REQ-002` (constraint) Adjacent subsystem ownership boundaries - Settings may expose workflows for Secrets, Provider Profiles, Operations, and runtime strategies, but those systems remain authoritative for their semantics. Source: 2. Document Boundaries.
- `DESIGN-REQ-003` (requirement) Backend-owned declarative catalog - The backend owns catalog descriptors, setting types, validation, sensitivity, authorization, and effective-value resolution. Source: 5.1 Backend-Owned Truth; 8. Settings Catalog Contract.
- `DESIGN-REQ-004` (security) Explicit exposure and eligibility - Settings become editable only through explicit exposure metadata and eligibility rules; sensitive or ambiguous fields default to hidden. Source: 5.2 Explicit Exposure; 9. Eligibility Rules.
- `DESIGN-REQ-005` (artifact) Stable setting keys and descriptor shape - Setting keys are stable dotted identifiers and descriptors expose durable typed metadata including scopes, values, options, constraints, reload behavior, and audit policy. Source: 7.1 Setting Key; 8.1 Descriptor Shape; 8.4 Catalog Stability.
- `DESIGN-REQ-006` (state-model) Scoped overrides and reset inheritance - User and workspace changes are sparse scoped overrides; reset deletes an override and returns to inherited defaults or higher-scope values. Source: 4. Scoped Overrides; 7.3-7.5; 11.4 Deletion and Reset.
- `DESIGN-REQ-007` (state-model) Explainable effective values - Effective values must resolve defaults, config/environment, overrides, provider profiles, SecretRefs, and locks with source explanations and explicit missing-value diagnostics. Source: 5.5 Explainability; 10. Resolution Model.
- `DESIGN-REQ-008` (integration) Settings API contracts - Catalog, effective, update, reset, validation, preview, audit APIs and structured errors define the external contract for settings clients. Source: 12. API Contract.
- `DESIGN-REQ-009` (requirement) Generated UI controls and page behaviors - The UI renders supported controls from descriptors and provides search, filtering, scope switching, preview, save/discard, reset, source badges, and audit links. Source: 9.2 Supported Generic UI Types; 13. UI Contract.
- `DESIGN-REQ-010` (security) Secret-safe generic settings behavior - Generic setting overrides store SecretRefs or resource references, never plaintext secrets, OAuth state, decrypted files, or generated credential config. Source: 5.3 References Over Secrets; 10.4 SecretRef Resolution; 22. Security Requirements.
- `DESIGN-REQ-011` (integration) Managed secrets workflows from Settings - Settings exposes managed secret create, replace, rotate, disable, delete, validate, usage, and copy-SecretRef workflows without plaintext readback. Source: 14. Secrets Integration.
- `DESIGN-REQ-012` (integration) Provider profile configuration and readiness - Provider Profiles are first-class resources in Providers & Secrets, with specialized profile editing, role-aware SecretRef binding, readiness, defaults, and routing. Source: 15. Provider Profiles Integration.
- `DESIGN-REQ-013` (requirement) Operations as commands, not preferences - Operational controls are explicit commands or statusful controls with state, authorization, confirmation, audit events, idempotency, and rollback/resume paths. Source: 17. Operations Settings.
- `DESIGN-REQ-014` (security) Granular authorization model - Reading metadata, writing user/workspace settings, managing secrets, managing provider profiles, invoking operations, and reading audit logs require distinct permissions. Source: 5.6 Least Privilege; 20. Authorization Model.
- `DESIGN-REQ-015` (observability) Auditable and redacted changes - Settings changes must produce audit records that capture actor, scope, values where allowed, redaction status, validation outcome, apply mode, and affected systems. Source: 11.2 Settings Audit Table; 21. Audit and Observability.
- `DESIGN-REQ-016` (state-model) Runtime application and reload semantics - Settings declare apply modes, emit change events, support subscriber refresh/reload behavior, and make pending restart requirements visible. Source: 19. Change Application Semantics.
- `DESIGN-REQ-017` (state-model) Settings override persistence model - Overrides and audit events use scoped durable rows with version fields, uniqueness constraints, allowed JSON values, and explicit prohibited data classes. Source: 11. Persistence Model.
- `DESIGN-REQ-018` (security) Security invariants for APIs and persistence - Unknown keys, client-supplied descriptor metadata, unsupported scopes, operator-locked writes, oversized values, executable object payloads, and unprotected APIs must be rejected. Source: 22. Security Requirements; 29. Desired-State Invariants.
- `DESIGN-REQ-019` (durability) Backup and recovery behavior - Backups include non-sensitive settings data, SecretRefs, resource references, audit records, and metadata, but not raw managed secret plaintext; broken references after restore must be surfaced. Source: 23. Backup and Recovery.
- `DESIGN-REQ-020` (migration) Migration and deprecation semantics - Renaming, removing, or changing setting types requires explicit migration, deprecation diagnostics, tests, and no silent loss or ambiguous reinterpretation. Source: 24. Migration and Deprecation.
- `DESIGN-REQ-021` (requirement) Required test coverage - Tests must cover catalog generation, sensitive-field exclusion, validation, inheritance, locks, reset, audit redaction, provider/SecretRef validation, UI controls, operations auth, and catalog drift. Source: 25. Testing Requirements.
- `DESIGN-REQ-022` (integration) Extensible component architecture - Backend and frontend components should support descriptor reuse by Settings UI, API clients, CLI tooling, tests, diagnostics, onboarding, docs, and future integrations. Source: 5.9 Durable Contracts; 26. Suggested Internal Components; 28. Open Integration Points.
- `DESIGN-REQ-023` (requirement) Local-first configuration baseline - A personal or local deployment starts with minimal prerequisites and lets users configure secrets and settings through Mission Control. Source: 3. Goals; README Quick Start alignment.
- `DESIGN-REQ-024` (non-goal) Explicit non-goals - The Settings System is not a generic database editor, generic secret manager, raw env editor, generic admin UI, provider-profile replacement, or Operations preference layer. Source: 4. Non-Goals.
- `DESIGN-REQ-025` (observability) Fail-fast diagnostics - Invalid settings, missing SecretRefs, broken profile bindings, locked values, unsupported scopes, validation failures, restart needs, and launch blockers produce actionable diagnostics without silent sensitive fallback. Source: 5.7 Fail Fast; 10.5 Missing Values; 21.3 Diagnostics.
- `DESIGN-REQ-026` (state-model) User and workspace policy inheritance - User settings inherit workspace defaults unless explicitly overridden and permitted by workspace policy constraints. Source: 16. User / Workspace Settings.
- `DESIGN-REQ-027` (integration) Open integrations preserve core invariants - Future exporters, CLI management, docs generation, templates, imports, policy-as-code, RBAC, drift detection, and external secret managers must preserve descriptor-driven exposure, scoped overrides, validation, auditability, and secret safety. Source: 28. Open Integration Points.
- `DESIGN-REQ-028` (constraint) Desired-state invariants are enforceable - The system is correct only if catalog exposure, scope declarations, validation, secret safety, explainability, reset inheritance, operator locks, operational audit, and intentional catalog changes hold together. Source: 29. Desired-State Invariants.

## Ordered Story Candidates

### STORY-001: Settings catalog and effective-value contract

Short name: `settings-catalog-contract`
Source reference: `docs/Security/SettingsSystem.md` sections 1. Summary, 5. Core Principles, 7. Key Concepts, 8. Settings Catalog Contract, 10. Resolution Model, 12. API Contract, 26. Suggested Internal Components
Coverage IDs: DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-022

As a MoonMind operator or settings client, I can read a backend-owned settings catalog and effective-value explanations so configuration surfaces are discoverable, typed, scoped, and authoritative.

Scope:
- Implement descriptor registration/generation for explicitly described settings.
- Return catalog descriptors grouped by section/category and effective values with source explanations.
- Expose structured catalog, effective, validation, preview, update/reset, and error contract primitives required by downstream stories.

Out of scope:
- Mission Control visual rendering
- Managed secret value storage
- Operational command execution

Independent test: Call Settings catalog and effective-value APIs against a seeded registry and assert descriptor shape, stable keys, scopes, source explanations, and structured errors without rendering the UI.

Acceptance criteria:
- Given an explicitly exposed setting, the catalog returns its stable key, type, section, category, scopes, constraints, source, reload metadata, dependency metadata, and audit policy.
- Given an unexposed backend setting, the catalog omits it and write attempts return `setting_not_exposed`.
- Given defaults, config/environment inputs, workspace overrides, user overrides, SecretRefs, provider-profile references, and operator locks, the effective API returns the winning value and its source explanation.
- Given missing defaults, null inherited values, unresolved SecretRefs, missing provider profile references, policy-blocked values, or invalid migrated values, the resolver returns explicit diagnostics instead of silently falling back.
- Settings API errors use the documented structured error shape for unknown keys, invalid scopes, read-only settings, operator locks, invalid values, version conflicts, and permission failures.

Requirements:
- The backend is the authority for descriptor metadata and effective-value resolution.
- Catalog responses are reusable by UI, API clients, CLI tooling, tests, diagnostics, onboarding, and documentation generators.
- Descriptor keys and option values remain stable durable API contract values.

Dependencies: None
Assumptions:
- Existing FastAPI and Pydantic v2 conventions can represent descriptor and effective-value models.
Needs clarification: None

### STORY-002: Scoped override persistence and inheritance

Short name: `scoped-settings-overrides`
Source reference: `docs/Security/SettingsSystem.md` sections 3. Goals, 4. Scoped Overrides, 7.3 Scope, 7.4 Override, 7.5 Effective Value, 11. Persistence Model, 16. User / Workspace Settings
Coverage IDs: DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-026

As a workspace admin or user, I can save, inspect, and reset scoped settings overrides so user and workspace configuration inherits predictably without mutating built-in defaults.

Scope:
- Persist user and workspace overrides separately from defaults, config files, and environment sources.
- Support versioned update and reset semantics for sparse overrides.
- Make workspace policy constraints visible when user settings inherit or are blocked.

Out of scope:
- Provider Profile resource editing
- Managed secret plaintext storage
- Catalog UI rendering

Independent test: Exercise override store and resolver integration with seeded defaults, workspace policies, and user overrides, then reset an override and verify the inherited effective value returns.

Acceptance criteria:
- Given no override, the effective value inherits from the default or higher configured source.
- Given a workspace override, task/workspace resolution reflects the workspace value and records version metadata.
- Given an allowed user override, user resolution wins over workspace inheritance while preserving workspace policy constraints.
- Given reset of a user or workspace override, the override row is deleted and defaults, provider profiles, managed secrets, OAuth volumes, and audit history are not deleted.
- Given conflicting expected versions, update returns `version_conflict` and does not persist a partial change.

Requirements:
- Override storage must enforce unique scope/workspace/user/key rows.
- Override rows may store only allowed JSON values, SecretRefs, and resource references.
- Override rows must never store raw secrets, OAuth state blobs, decrypted files, generated credential config, large artifacts, workflow payloads, or operational history beyond audit metadata.

Dependencies: STORY-001
Assumptions:
- If existing settings storage tables differ from the desired-state shape, this story adapts them only when they preserve the documented semantics.
Needs clarification: None

### STORY-003: Generated User and Workspace settings UI

Short name: `generated-settings-ui`
Source reference: `docs/Security/SettingsSystem.md` sections 3. Goals, 6. Settings Page Topology, 9. Eligibility Rules, 13. UI Contract, 16. User / Workspace Settings, 27. Example End-to-End Flows
Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-023

As a Mission Control user, I can configure eligible user and workspace settings through generated controls so local-first configuration is discoverable without bespoke forms for every setting.

Scope:
- Render Settings page navigation with Providers & Secrets, User / Workspace, and Operations sections.
- Generate User / Workspace controls from backend descriptors for supported UI types.
- Support page-level search, category filters, scope switching, modified-only/read-only filters, preview, save/discard, reset, source explanation, and audit link entry points.

Out of scope:
- Specialized Provider Profile editor internals
- Managed secret value entry flows
- Actual worker pause/resume command execution

Independent test: Render Mission Control Settings with a mocked catalog containing each supported generic UI type and assert controls, filters, preview, reset, source badges, and local-first onboarding states behave correctly.

Acceptance criteria:
- Given descriptors for boolean, string, bounded number, enum, list, key/value, SecretRef, and read-only settings, the UI renders the documented controls and submits only user intent.
- Given a read-only or operator-locked descriptor, the UI displays the lock reason and does not enable ordinary editing.
- Given a modified setting, the UI can preview validation and affected subsystem information before save.
- Given an overridden value, the UI shows reset-to-inherited behavior and source badges using documented labels.
- A fresh local deployment can reach Settings and configure initial non-secret settings and SecretRef bindings through Mission Control without requiring hand-edited frontend forms.

Requirements:
- The frontend must not decide backend eligibility, validation, sensitivity, or authorization.
- Ineligible, secret, or operator-only settings must remain hidden, read-only, or routed to specialized managers according to backend descriptors.
- Generic settings UI must not allow direct environment-variable editing from the browser.

Dependencies: STORY-001, STORY-002
Assumptions:
- Mission Control already has a Settings entrypoint that can be extended rather than replaced wholesale.
Needs clarification: None

### STORY-004: Secret-safe Settings and Managed Secrets workflows

Short name: `secret-safe-settings`
Source reference: `docs/Security/SettingsSystem.md` sections 2.2 What the Secrets System owns, 5.3 References Over Secrets, 7.9 SecretRef Setting, 10.4 SecretRef Resolution, 14. Secrets Integration, 22. Security Requirements
Coverage IDs: DESIGN-REQ-002, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-018

As a secret manager or workspace admin, I can create and bind managed secrets from Settings while generic settings store only SecretRefs and never reveal plaintext after submission.

Scope:
- Expose Managed Secrets metadata workflows from Settings while delegating storage, encryption, resolution, rotation, revocation, and plaintext redaction to the Secrets System.
- Implement SecretRef picker semantics for generic settings and provider/integration bindings.
- Validate SecretRefs and show usage/broken-reference state without resolving plaintext into settings rows or browser responses.

Out of scope:
- Redefining Secrets System storage semantics
- Replacing Provider Profiles with generic secret settings
- Adding a reveal-secret action

Independent test: Submit managed secret creation/replacement and SecretRef setting updates through Settings APIs/UI, then assert plaintext is accepted only in one-way secret flows, cleared from UI state, absent from overrides/audit/readback, and represented by SecretRef metadata.

Acceptance criteria:
- Generic overrides reject API keys, access tokens, refresh tokens, passwords, private keys, OAuth state, and credential-bearing generated config.
- Secret-like backend fields are hidden unless explicitly represented as SecretRef pickers or managed through the Managed Secrets creation/replacement flow.
- Managed secret create and replace flows accept plaintext only as one-way submissions, then clear browser input and show metadata plus SecretRef.
- Secret validation resolves plaintext only in memory at controlled execution boundaries, discards it, stores redacted metadata, and returns redacted diagnostics.
- Broken, disabled, revoked, or missing SecretRefs are surfaced clearly and prevent affected launches where appropriate.

Requirements:
- Settings must not redefine SecretRef semantics, secret storage, encryption at rest, root key custody, backend classes, resolution, rotation, revocation, audit, or plaintext redaction rules.
- SecretRef values are security-relevant metadata and must be access controlled.
- Settings APIs must ignore client-supplied descriptor metadata and enforce session/CSRF protections appropriate to MoonMind auth.

Dependencies: STORY-001, STORY-002
Assumptions:
- The existing Secrets System remains the authoritative implementation for encrypted storage and secret resolution.
Needs clarification: None

### STORY-005: Provider Profile management and readiness in Settings

Short name: `provider-profile-settings`
Source reference: `docs/Security/SettingsSystem.md` sections 2.3 What Provider Profiles own, 6.1 Providers & Secrets, 10.3 Provider Profile Resolution, 15. Provider Profiles Integration, 27.2 Add GitHub Token
Coverage IDs: DESIGN-REQ-002, DESIGN-REQ-012, DESIGN-REQ-025

As a workspace admin, I can manage provider profiles in Settings and see readiness diagnostics while Provider Profiles remain the execution contract for runtime/provider launch semantics.

Scope:
- Add specialized Provider Profile forms and list/detail surfaces inside Providers & Secrets.
- Support profile create, update, delete, validate, enable, disable, default selection, role-aware SecretRef binding, and readiness state.
- Allow User / Workspace settings to reference provider profiles or selectors without inlining profile semantics into generic settings.

Out of scope:
- Replacing provider-profile execution semantics with generic settings
- Resolving plaintext secrets in settings rows
- Runtime-specific command construction

Independent test: Create and validate provider profiles with required fields, SecretRef bindings, OAuth volume status, enabled/disabled state, cooldown, and concurrency conditions, then assert readiness diagnostics and default profile references are reflected in Settings.

Acceptance criteria:
- Provider profile editing exposes runtime, provider, credential source class, materialization mode, default model, overrides, SecretRef role bindings, OAuth volume metadata, concurrency, cooldown, tags, priority, default status, and readiness where applicable.
- Provider profile readiness combines schema validity, required fields, SecretRef resolvability, OAuth volume status, provider validation, enabled state, concurrency availability, and cooldown state.
- A workspace or user setting may select a provider profile reference, but generic setting values do not inline runtime launch semantics.
- Missing provider profile references return explicit diagnostics and launch blockers rather than silent fallback.
- Runtime strategies still own command construction, environment shaping, generated runtime files, process launch, and capability checks.

Requirements:
- Provider Profiles remain first-class resources inside Providers & Secrets.
- Role-aware SecretRef pickers must describe what the profile needs and where the value can be resolved.
- Readiness state must be observable before affected launches run.

Dependencies: STORY-001, STORY-004
Assumptions:
- Existing provider profile models can be surfaced through descriptor-assisted specialized forms.
Needs clarification: None

### STORY-006: Operations controls exposed as authorized commands

Short name: `operations-command-settings`
Source reference: `docs/Security/SettingsSystem.md` sections 2.4 What Operations own, 6.3 Operations, 17. Operations Settings, 20. Authorization Model, 27.4 Pause Workers
Coverage IDs: DESIGN-REQ-002, DESIGN-REQ-013, DESIGN-REQ-014

As an operator, I can invoke operational controls from Settings with current state, impact, confirmation, authorization, audit trail, and rollback or resume feedback instead of editing ordinary preferences.

Scope:
- Expose operational status and controls in the Settings Operations section.
- Model pause, resume, drain, quiesce, maintenance mode, launch scheduling, reason text, and operational banners as explicit commands or statusful controls.
- Require permission checks, confirmations for disruptive actions, idempotency keys, audit events, result statuses, and rollback/resume paths where available.

Out of scope:
- Treating operations as generic key/value preferences
- Owning worker pause/drain/quiesce semantics inside Settings
- Bypassing operation subsystem state

Independent test: Invoke an Operations command through the Settings surface with and without required authorization/confirmation and assert state feedback, command payload, idempotency, audit event, failure reason, and resume path behavior.

Acceptance criteria:
- Operational controls show current state, command impact, confirmation requirements, actor authorization, last action and actor, pending transitions, failure reason, and safe rollback/resume action where available.
- Actions that stop active work, prevent launches, affect all workers/runtimes, delete data, revoke credentials, or change global routing require confirmation.
- Operations commands include actor, target, requested state, reason, confirmation state, timestamp, idempotency key, audit event, result status, and rollback/resume path where possible.
- Unauthorized users cannot invoke operations even if frontend controls are hidden or manipulated.
- Operational subsystems remain authoritative for worker, queue, scheduler, health, and destructive/disruptive semantics.

Requirements:
- Settings exposes Operations for discoverability only; Operations remains the semantic owner of command effects.
- Operations controls are auditable actions, not mutable preferences.
- The UI must communicate expected effect before the command is submitted.

Dependencies: STORY-001
Assumptions:
- Existing operational subsystems expose command or service methods that Settings can call instead of duplicating behavior.
Needs clarification: None

### STORY-007: Authorization, audit, redaction, and diagnostics

Short name: `settings-audit-diagnostics`
Source reference: `docs/Security/SettingsSystem.md` sections 5.6 Least Privilege, 5.7 Fail Fast, 11.2 Settings Audit Table, 12.6 Audit APIs, 20. Authorization Model, 21. Audit and Observability, 22. Security Requirements
Coverage IDs: DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-018, DESIGN-REQ-025

As an auditor or authorized operator, I can inspect settings changes and diagnostics with least-privilege permissions and redaction so configuration changes are accountable without exposing sensitive values.

Scope:
- Enforce permission checks for every write and sensitive metadata read.
- Write settings audit events with descriptor-driven redaction and expose authorized audit/history APIs and UI links.
- Provide diagnostics explaining read-only state, effective source, recent changes, validation failures, restart needs, missing provider profiles/secrets, and launch readiness blockers.

Out of scope:
- Plaintext secret audit storage
- Broad enterprise RBAC redesign beyond the documented permission surface
- Operations command implementation internals

Independent test: Run authorized and unauthorized settings, secret metadata, provider profile, operations, and audit-read flows, then assert audit records, redaction, diagnostics, and permission failures match the documented model.

Acceptance criteria:
- Permission checks distinguish catalog read, effective read, user write, workspace write, system read/write, secret metadata read, secret value write, secret rotation/disable/delete, provider profile write, operations invoke, and audit read.
- Audit records include setting key, scope, actor, permitted old/new values, redaction status, request/source metadata where available, reason, validation outcome, apply mode, and affected systems.
- Audit values redact raw secrets, sensitive generated config, OAuth state, private keys, token-like values, provider-returned sensitive diagnostics, and descriptor-redacted values.
- SecretRef values are recorded only when authorized by policy and treated as security-relevant metadata.
- Diagnostics answer why a setting is read-only, why a value is effective, where it came from, what changed, why validation failed, what needs restart, and which missing profile/secret/setting blocks launch readiness.

Requirements:
- Frontend-hidden controls are not a security boundary.
- Settings changes must be auditable without exposing sensitive values.
- Fail-fast diagnostics must be actionable and must not silently fall back to another sensitive source.

Dependencies: STORY-001, STORY-002, STORY-004, STORY-006
Assumptions:
- Existing auth/session services can be used for permission checks and CSRF/session protections.
Needs clarification: None

### STORY-008: Change application, reload, restart, and recovery semantics

Short name: `settings-change-lifecycle`
Source reference: `docs/Security/SettingsSystem.md` sections 18. Validation Model, 19. Change Application Semantics, 23. Backup and Recovery, 27. Example End-to-End Flows
Coverage IDs: DESIGN-REQ-016, DESIGN-REQ-019, DESIGN-REQ-025

As an operator, I can understand when settings take effect and recover from backup/restore reference gaps so runtime behavior changes are visible, durable, and safe.

Scope:
- Declare and enforce setting apply modes: immediate, next_request, next_task, next_launch, worker_reload, process_restart, and manual_operation.
- Emit structured change events and support subscriber refresh/reload behavior for Mission Control, task creation, provider profile manager, workers, and operational controls.
- Expose backup/restore-safe diagnostics for settings whose referenced secrets, OAuth volumes, or provider profiles are missing after recovery.

Out of scope:
- Implementing every subscriber reload in all runtime strategies at once
- Storing raw secrets in backups
- Changing Secrets System backup semantics

Independent test: Commit settings with different apply modes, observe emitted change events and UI restart/reload badges, then simulate restore with missing references and assert broken-reference diagnostics appear without plaintext leakage.

Acceptance criteria:
- Each setting declares how changes apply and descriptors expose reload, worker restart, process restart, and affected subsystem metadata.
- Committed changes emit structured events containing event type, key, scope, source, apply mode, actor, and timestamp.
- Consumers can refresh catalog state, use updated task defaults, sync profile-related changes, reload non-disruptive worker settings, and update operational status where applicable.
- When restart is required, the UI shows current effective value, pending value if applicable, affected process or worker, whether active, and how to complete activation.
- After restore without corresponding secrets, OAuth volumes, or provider profiles, Settings surfaces broken references clearly while backups exclude raw managed secret plaintext.

Requirements:
- Validation must occur at descriptor generation, write receipt, before persistence, after persistence during preview, before launch or operation execution, and during readiness diagnostics where applicable.
- Backups may contain setting keys, non-sensitive values, SecretRefs, resource references, audit records, and metadata only.
- Runtime application behavior must be observable rather than implicit.

Dependencies: STORY-001, STORY-002, STORY-007
Assumptions:
- Existing event or notification infrastructure can carry settings change events to relevant consumers.
Needs clarification: None

### STORY-009: Migration, non-goals, invariants, and test gate

Short name: `settings-invariant-gate`
Source reference: `docs/Security/SettingsSystem.md` sections 4. Non-Goals, 24. Migration and Deprecation, 25. Testing Requirements, 28. Open Integration Points, 29. Desired-State Invariants
Coverage IDs: DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-024, DESIGN-REQ-027, DESIGN-REQ-028

As a maintainer, I can evolve the Settings System safely because migrations, non-goals, future integrations, and desired-state invariants are enforced by tests and diagnostics rather than tribal knowledge.

Scope:
- Add migration/deprecation handling for renamed, removed, or type-changed settings with explicit diagnostics.
- Codify non-goals and desired-state invariants as regression tests and catalog drift checks.
- Define extension expectations for future exporters, CLI management, documentation generation, templates, import/export, policy-as-code, RBAC, drift detection, multi-workspace inheritance, external secret managers, subscriptions, and provider validation.

Out of scope:
- Implementing all open integration points
- Creating a generic admin UI
- Guaranteeing hot reload for every setting
- Replacing specialized subsystem contracts

Independent test: Run the Settings System test gate against seeded descriptors and migrations, including catalog drift snapshots, sensitive-field exclusion, invalid write cases, inheritance, locks, reset, audit redaction, provider/SecretRef validation, UI controls, and Operations authorization.

Acceptance criteria:
- Renaming a setting uses a new descriptor key, migrates old overrides, exposes deprecation diagnostics where helpful, records audit visibility, and tests effective-value preservation.
- Removing a setting rejects new writes, preserves or migrates existing values, explains deprecated values in diagnostics, and avoids silent loss of operator intent.
- Changing a setting type requires explicit migration and the resolver does not ambiguously reinterpret existing JSON values.
- Regression coverage proves catalog exposure, scope declarations, backend validation, secret safety, SecretRef validation/audit without plaintext, provider profile secret references, source explainability, reset inheritance, operator locks, operational audit, and intentional catalog changes.
- Future integration contracts explicitly preserve descriptor-driven exposure, scoped overrides, server-side validation, auditability, and secret-safe behavior.

Requirements:
- The Settings System must not become a generic database editor, generic secret manager, raw env editor, generic admin UI, or frontend-authoritative validation layer.
- Catalog drift must be visible through snapshot or equivalent regression tests.
- Non-goals and invariants must remain enforceable as the implementation evolves.

Dependencies: STORY-001, STORY-002, STORY-004, STORY-005, STORY-006, STORY-007, STORY-008
Assumptions:
- Snapshot tests are acceptable for detecting accidental descriptor drift in addition to focused unit and integration tests.
Needs clarification: None

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-003
- `DESIGN-REQ-002` -> STORY-004, STORY-005, STORY-006
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-003
- `DESIGN-REQ-005` -> STORY-001
- `DESIGN-REQ-006` -> STORY-002
- `DESIGN-REQ-007` -> STORY-001
- `DESIGN-REQ-008` -> STORY-001
- `DESIGN-REQ-009` -> STORY-003
- `DESIGN-REQ-010` -> STORY-004
- `DESIGN-REQ-011` -> STORY-004
- `DESIGN-REQ-012` -> STORY-005
- `DESIGN-REQ-013` -> STORY-006
- `DESIGN-REQ-014` -> STORY-006, STORY-007
- `DESIGN-REQ-015` -> STORY-007
- `DESIGN-REQ-016` -> STORY-008
- `DESIGN-REQ-017` -> STORY-002
- `DESIGN-REQ-018` -> STORY-004, STORY-007
- `DESIGN-REQ-019` -> STORY-008
- `DESIGN-REQ-020` -> STORY-009
- `DESIGN-REQ-021` -> STORY-009
- `DESIGN-REQ-022` -> STORY-001
- `DESIGN-REQ-023` -> STORY-003
- `DESIGN-REQ-024` -> STORY-009
- `DESIGN-REQ-025` -> STORY-005, STORY-007, STORY-008
- `DESIGN-REQ-026` -> STORY-002
- `DESIGN-REQ-027` -> STORY-009
- `DESIGN-REQ-028` -> STORY-009

## Dependencies

- `STORY-001` depends on None.
- `STORY-002` depends on STORY-001.
- `STORY-003` depends on STORY-001, STORY-002.
- `STORY-004` depends on STORY-001, STORY-002.
- `STORY-005` depends on STORY-001, STORY-004.
- `STORY-006` depends on STORY-001.
- `STORY-007` depends on STORY-001, STORY-002, STORY-004, STORY-006.
- `STORY-008` depends on STORY-001, STORY-002, STORY-007.
- `STORY-009` depends on STORY-001, STORY-002, STORY-004, STORY-005, STORY-006, STORY-007, STORY-008.

## Out-of-Scope Items and Rationale

- No `spec.md` files or `specs/` directories are created during breakdown; each story is a future candidate for specify.
- The Settings System is not a generic database editor, raw environment editor, generic secret manager, generic admin UI, Provider Profile replacement, Operations preference system, or frontend-authoritative validation layer.
- Secrets, Provider Profiles, Operations, and runtime strategies remain authoritative for their specialized semantics; Settings exposes safe workflows and references.
- Open integration points such as CLI settings management, generated docs, templates, external secret managers, and subscriptions are future extensions that must preserve the core invariants.

## Coverage Gate

PASS - every major design point is owned by at least one story.

## Recommended First Story

Run `/speckit.specify` first for `STORY-001: Settings catalog and effective-value contract` because it establishes the descriptor, API, and resolver contract that later UI, persistence, secret, provider, operations, and audit stories depend on.
