# Settings System

MoonMind is a single-user application: one operator per instance. Settings provide backend-owned configuration metadata, validated overrides, source explanations, redacted audit, and explicit activation behavior. They do not introduce tenants, memberships, human roles, or a mandatory workspace record.

## Single-user scope authority

The [single-user application design](../SingleUserApplicationDesign.md#5-instance-settings-presets-and-preferences) defines the target: instance overrides with meaningful project, repository, provider, and runtime contexts where needed. These contexts are configuration inputs, not human ownership boundaries. Operator admission and scoped machine authority preserve access controls, locked values, secret confidentiality, and operation-specific policy.

[Settings routes](../../api_service/api/routers/settings.py) read and change one instance configuration through the shared instance admission, without resolving a person or a synthetic subject. No current setting declares a project, repository, provider, or runtime context, so requests carry no scope. An instance override row has null `scope`, `workspace_id` and `user_id`; new audit events carry no subject or actor, only reason and request id. Shared operator admission protects settings, Provider Profiles, Managed Secrets and Operations. Retired account sessions grant no access. Server-side locks, read-only descriptors, operation availability and validation protect values and commands; the dashboard carries no human permission list.

Rows with a subject are retained predecessor data. Instance reads, audit history, consumers and the broken-reference scan ignore them, while backups and the secret-deletion guard still include them. Revision 398 rewrites only the deployment-subject (zero-UUID) workspace rows, which already were the instance configuration. Personal and other context rows wait for the `settings` transform in the [guarded conversion](../../api_service/services/single_user_conversion.py), which runs after attribution and writer exclusion. A personal row overrode its workspace for that person, so for the proven operator it becomes the instance value; shared rows decide only keys without one. An instance write or reset made after revision 398 (an override event with null scope) is a later explicit choice, so it joins the deciding tier instead of losing to a retained row; a reset cannot be named in a disposition, so the operator names a retained value and resets again after conversion if needed. Equal choices merge, and the merged version advances past every prior version. Distinct choices within the deciding tier refuse with a redacted `settings_collision` until the operator's disposition names one of those values. Superseded values stay in the `settings.override.converted` audit event. Multi-person sources stay untouched under the [upgrade eligibility and disposition](../SingleUserApplicationDesign.md#upgrade-eligibility-and-disposition), and historical actor identifiers remain provenance only.

## Responsibilities and sources

- [Settings registry and service](../../api_service/services/settings_catalog.py): descriptors, eligibility, effective values, validation, overrides, diagnostics, and audit read models.
- [Settings API](../../api_service/api/routers/settings.py): authenticated catalog/effective reads, preview, validation, updates, reset, and audit access.
- [Application configuration](../../moonmind/config/settings.py): typed deployment configuration and environment inputs.
- [Settings Configuration Pages](../UI/SettingsPage.md): navigation and UI composition. Existing `user-workspace` labels do not define a new tenancy model.
- [Secrets System](./SecretsSystem.md): SecretRefs, encryption, root-key custody, resolution, rotation, and plaintext redaction. Settings can select references or expose specialized secret management, but never read stored plaintext back into a generic form.
- [Provider Profiles](./ProviderProfiles.md): runtime/provider selection, credential source, materialization, concurrency, and readiness. A generic setting can reference a profile, not duplicate its execution semantics.
- [Workflow Publishing](../Workflows/WorkflowPublishing.md): publication selection and composition defaults. Generic settings precedence is not a second publication compiler.
- [Operations](../Steps/DockerComposeUpdateSystem.md): explicit commands and observable outcomes, rather than key/value preferences that pretend to pause, drain, or update workers.

Omnigent settings bootstrap the persistent [policy authority](../Omnigent/PolicyAuthority.md); environment values are not mutable per-run policy. The Codex product path is `agentKind=external`, `agentId=omnigent`, nested harness `codex-native`; see the [Create-to-host contract](../Omnigent/CodexCreateToHostContract.md).

## Catalog and validation

A descriptor owns a stable key, type and constraints, UI representation, allowed configuration context, source/override information, sensitivity, lock reason, audit policy, and apply mode. Use the executable `SettingRegistryEntry` and response models for exact fields rather than maintaining a parallel schema here.

Explicit backend exposure is required before a field becomes editable. The frontend renders descriptors and submits values; it cannot authorize a field by supplying its own descriptor. Unknown keys, unsupported contexts, invalid values, size violations, and policy-incompatible combinations are rejected server-side. Secret-like name heuristics provide defense in depth, not permission to expose a field. Deployment environment mutation, credential files, OAuth blobs, large artifacts, and operational command history do not belong in ordinary overrides.

Generic controls cover supported scalar, enum, list, and small structured values. A complex domain resource belongs in its specialized editor. Secret-typed inputs use SecretRef selection; plaintext entry belongs only in managed-secret creation/replacement flows. Reference metadata remains access-controlled even though it is not the secret itself.

Validate at write and pre-persistence boundaries, during effective-value preview, and again where launch or operational authority is exercised. Cross-setting checks include profile availability, permitted runtime/secret backends, and operation policy. A successful save does not prove launch readiness or activation.

## 10. Resolution Model

The target resolution is built-in/deployment defaults, then applicable instance/context overrides, with explicit operator locks winning. An override does not mutate the underlying default. Reset removes the override and reveals the remaining source; it is not a write of the presently inherited value.

`SettingsCatalogService._resolve_value_from_overrides` gives a lock precedence, then the instance override (`instance_override`), then rename-aware migration handling and configured defaults. `_resolve_value` checks declared environment aliases before the configured settings path and descriptor default. Source badges report the actual resolved source.

Distinguish absence, an intentional null override, an inherited null, missing defaults, a broken reference, a policy block, and invalid migrated data. Diagnostics explain fallback or blocked readiness rather than silently presenting an invalid value as usable. Settings resolution returns a SecretRef, never its plaintext. The Secrets System resolves it only at controlled validation or execution boundaries.

### 10.6 Publication Default Ownership and Retired Setting

**Target behavior, not a completed migration.** The current registry still exposes this setting and the current Python configuration retains its aliases. The single-publication-compiler design removes the active `workflow.default_publish_mode` setting and its `WORKFLOW_DEFAULT_PUBLISH_MODE` and `MOONMIND_DEFAULT_PUBLISH_MODE` environment aliases. It does not demote them to a fallback that still applies when a preset lacks metadata or a helper omits a field. Under that target, the single publication compiler is authoritative for Create, API/MCP, preset expansion, schedules, Edit/Rerun, and scoped child admission.

After the cutover, an explicit supported workflow selection is preserved for new authoring. Omission and `default` both mean the same composition-specific Auto, resolved from pinned definition metadata and task inputs. Scoped children inherit the authenticated parent's frozen intent. Settings-owned allowed-operation/approval constraints may reject incompatible effects, but may not rewrite Auto to None/Branch/PR or silently alter an explicit choice. An unresolved declaration is a compiler error, not permission to consult the retired setting.

Retirement preserves configured operator intent through the existing settings migration/audit owner:

- Reject new writes to the removed setting with the established removed-key error. Do not expose an active Default Publish Mode control, return its value as an active fallback, or prefill a workflow with it as though it were explicitly selected.
- Retain existing configured override rows as diagnostic-only evidence under [removal semantics](#242-removing-settings), with settings.migration.removed audit events. Detect explicitly configured legacy environment values as well as database overrides. Their continued presence is actionable retirement state, not runtime authority.
- Preserve original admitted histories and recorded effective values. Their frozen decoder must not read today's workspace or environment fallback.
- For saved drafts/definitions whose recorded input origin proves the fallback supplied their old publishing choice, preserve that choice as an explicit workflow selection with migration provenance and ordinary compatibility checks. Do not overwrite a previously explicit selection or an established child-policy distinction.
- A configured fallback without sufficient per-input provenance, a conflicting policy, or an unattended definition whose meaning cannot be preserved requires visible review before affected new/defaulted launches are enabled. Show the old configured source/value and the proposed composition result. Do not silently discard a configured None or Branch and start publishing PRs, or replace a PR workflow with None.
- The operator explicitly adopts composition Auto, saves a valid explicit policy for affected definitions, or retains an affected schedule blocked until reviewed. Record that disposition with existing setting/definition revisions and audit references. Resetting a diagnostic row alone does not certify every saved schedule or draft.
- Backup/restore preserves the retirement records and review state. Restoring an old override or environment configuration cannot reactivate the fallback or bypass required review. No permanent new per-run toggle, replacement default setting, or second publication-policy store is introduced.

The new contract requires a coordinated cutover of the backend descriptor/config field and aliases, Settings/UI/API readers, Create hydration, and preset/helper fallbacks. History and retirement readers remain only for identified consumers. Documentation does not enable that cutover or authorize bypassing current enforcement.


## Persistence, APIs, and concurrent changes

The `settings_overrides` and `settings_audit_events` models are the source for exact schema. Overrides hold small non-secret JSON values or references, schema/value versions, and provenance; a partial unique index allows one instance row per key. Audit holds event identity, key, reason, request id, and redacted before/after values.

The router provides `GET /api/v1/settings/catalog`, `/effective`, `/effective/{key}`, `/diagnostics` and `/audit`, `POST /validate` and `/preview`, `PATCH /api/v1/settings` for updates, and `DELETE /api/v1/settings/{key}` for reset. Writes use server validation and expected-version protection, so a stale version cannot overwrite another client's accepted change. Reset honors the same lock and authorization boundary as update.

## Activation and operations

Apply mode communicates when a saved value becomes active. The current service defines `immediate`, `next_request`, `next_workflow`, `next_launch`, `worker_reload`, `process_restart`, and `manual_operation`. The UI should show current and pending values, the affected consumer, and any activation step. In particular, saving a restart-requiring value is not confirmation that a running worker adopted it.

[Change publishing](../../api_service/services/settings_change_publisher.py), [subscribers](../../api_service/services/settings_change_subscribers.py), and [hooks](../../api_service/services/settings_change_hooks.py) own propagation. Inspect those consumers when changing application semantics; a frontend refresh alone does not update runtime configuration.

Operations remain explicit commands with target, reason where needed, confirmation for disruptive actions, and returned status/error evidence. Shared operator admission remains enforced, independently of backend availability and command validation. Never interpret an accepted command as proof that workers have drained or a restart has finished.

## Security and diagnostics

- Keep raw credentials out of overrides, workflow payloads, logs, generic settings responses, and audit values. Provider materialization does not persist decrypted values into settings rows.
- Honor backend lock and exposure decisions, session/CSRF protections appropriate to the authentication mode, size limits, and validation. A supplied key or UI label cannot expand authority.
- Redact sensitive before/after values, including during migration. Do not reveal provider-returned credential diagnostics.
- Explain why a field is read-only, which source won, why validation/readiness failed, which reference is missing, and whether reload or restart remains pending.
- Settings has no plaintext readback path. Secret creation, replacement, validation, and rotation stay with the specialized Secrets and Provider Profile owners.

## 23. Backup and Recovery

Back up settings overrides and audit with their dependent managed-secret and provider-profile records. Settings rows contain references, not managed-secret plaintext; restoring encrypted secret records separately requires the external root key described in [Secrets System](./SecretsSystem.md).

### Backup contents and exclusions

[export_settings_backup](../../api_service/services/settings_backup.py) produces a `SettingsBackupBundle` with keys, non-sensitive values, SecretRefs, resource references, versions, and audit/migration provenance. It excludes redaction-marked non-SecretRef override values and lists their keys in `excluded_keys`. SecretRef values remain references; malformed secret-reference values or redacted audit rows that still contain payloads raise `SettingsBackupViolation` instead of emitting a leaking bundle.

Custom database backups must preserve the same confidentiality expectations. The Settings-only exporter is not a substitute for backing up encrypted secrets, provider profiles, OAuth credential volumes, or external key custody.

### Partial restore and recovery procedure

1. Take and verify a database backup covering settings overrides/audit and dependent secret/profile records. Use the Settings-only exporter for a portable redaction-checked snapshot when needed.
2. Restore dependencies before settings where possible. Retain migration audit events alongside the affected rows.
3. Run `scan_broken_references` from the backup service after a partial or staged restore. It reports instance SecretRefs and provider-profile references whose targets are missing, disabled, or invalid, and never substitutes another credential or profile.
4. Repair the dependency, select a valid reference, or reset the affected override before resuming dependent launches. Resolver diagnostics must remain visible; inherited/default fallback is not proof that the original dependency was restored.
5. Rehearse full and partial restores in an isolated environment, including settings without managed secrets and secrets without settings. Confirm diagnostics, preserved values, and absence of spurious audit changes.

A restored publication-default row follows [section 10.6](#106-publication-default-ownership-and-retired-setting). It must not reactivate a retired fallback or treat missing review provenance as consent to composition Auto.

## 24. Migration and Deprecation

Registry migration metadata and row migration are separate. `SettingMigrationRule` drives deprecation diagnostics, write rejection, and rename-aware resolution. [SettingsMigrationOrchestrator](../../api_service/services/settings_migrations.py) applies row changes and records audit. Neither the existence of a rule nor an updated document proves migration ran against a deployment.

### 24.1 Renaming Settings

`apply_rename` preserves the instance value, schema version, and value version at the new key, and records `settings.migration.renamed`. Retained predecessor rows are left for the guarded conversion. A pre-existing target raises `SettingsMigrationCollisionError` unless explicitly skipped. Preserve effective values and report collisions rather than overwriting accepted configuration.

### 24.2 Removing Settings

Removed/deprecated keys reject writes through `setting_not_exposed`. `apply_removal` retains existing rows for diagnostics and records `settings.migration.removed`; explicitly resetting those rows is a separate decision. For publication defaults, retained evidence is historical only after cutover, and a generic removal/reset cannot establish that every affected saved schedule or draft has been reviewed.

### 24.3 Changing Types

`apply_type_change` requires explicit `TypeChangeCoercion`, including a target schema version matching the migration rule. It never guesses how old JSON should be interpreted. Missing coercions are reported as skipped rules, and old-schema rows remain diagnosable as `post_migration_invalid`.

### 24.4 Audit and Backup Visibility

Keep rename, removal, and `settings.migration.type_changed` events in backups with their rows. Pass `redact_keys` for sensitive descriptors so migrations do not leak values into audit. Preserve configured intent through conversion; missing evidence is a reason to report a blocker, not permission to discard data or loosen policy.

## Verification references

Behavior coverage lives in [settings migration tests](../../tests/unit/services/test_settings_migrations.py), [backup tests](../../tests/unit/services/test_settings_backup.py), [instance conversion tests](../../tests/unit/services/test_settings_instance_conversion.py), and [backup/recovery integration coverage](../../tests/integration/api/test_settings_backup_recovery_contract.py). They exercise their own boundaries, not a deployment's conversion or the publication-default cutover; inspect deployment evidence before claiming either ran.
