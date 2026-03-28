# Secrets System

**Implementation tracking:** [`docs/tmp/010-SecretsSystemPlan.md`](../tmp/010-SecretsSystemPlan.md)

Status: **Design Draft**
Owners: MoonMind Engineering
Last Updated: 2026-03-27

> [!NOTE]
> This document defines the desired-state MoonMind Secrets System.
> It is a declarative contract for how secrets are referenced, stored, resolved, materialized, and audited.
> Phase sequencing, migration work, and implementation checklists belong in [`docs/tmp/010-SecretsSystemPlan.md`](../tmp/010-SecretsSystemPlan.md).

---

## 1. Summary

MoonMind needs a secrets system that is secure enough for multi-runtime orchestration while still matching the project's local-first, low-friction deployment model.

The Secrets System must therefore support both:

- simple operator-managed local deployments with no required external cloud dependency, and
- stronger optional integrations with external secret managers when an operator chooses them.

The baseline personal-use path must be simple:

- a user can launch `docker compose` successfully without creating or editing `.env`,
- open Mission Control after startup,
- enter a small number of secrets such as a provider API key and GitHub PAT through the UI or API, and
- run useful agent workloads without first setting up Vault, cloud KMS, or other heavyweight infrastructure.

The central design decision is:

> MoonMind stores and transports **secret references**, not raw secrets, across durable contracts.

That rule applies to:

- provider profiles,
- runtime profiles,
- workflow payloads,
- task definitions,
- scheduler state,
- logs, and
- durable run metadata.

Secret values are resolved only at controlled execution boundaries and materialized only for the narrow scope required by the runtime or outbound MoonMind-owned call path.

This document builds on [`ProviderProfiles.md`](./ProviderProfiles.md). Provider Profiles own provider selection and secret references. The Secrets System defines what those references mean and how they are safely resolved.

---

## 2. Goals

The Secrets System must support all of the following:

1. **SecretRef as a first-class contract**
   - Durable system contracts store references, not raw secret values.

2. **Local-first secure defaults**
   - Core deployments must not require AWS, GCP, Azure, Vault Cloud, or another mandatory external public-cloud secret service.

3. **Encrypted-at-rest managed secrets**
   - UI-entered or API-managed secrets must be stored encrypted at rest in MoonMind-controlled persistence.

4. **Multiple secret backends**
   - Operators may choose simple local sources or optional external managers.

5. **Launch-time resolution**
   - Secret values are resolved only when needed for runtime launch or MoonMind-owned outbound calls.

6. **Proxy-first provider access where possible**
   - When MoonMind owns an outbound provider call path, the caller should receive a MoonMind-controlled capability rather than a raw provider credential.

7. **Narrow runtime materialization**
   - When a third-party runtime truly requires raw credentials in-process, the system should materialize only the minimum required values for the shortest required scope.

8. **No raw secrets in durable artifacts**
   - Prompts, artifacts, workflow histories, run metadata, diagnostics, and generated durable config should not contain raw secret values.

9. **Operator observability without secret leakage**
   - Operators must be able to answer who set a secret, what references are in use, which backend resolved a value, and why a run failed, without exposing the secret itself.

10. **Rotation and revocation**
    - Secret values and backend credentials must be rotatable with clear operator-visible behavior.

---

## 3. Non-Goals

This design does **not** attempt to:

- require every deployment to run HashiCorp Vault or a cloud KMS,
- guarantee that no raw secret ever exists in process memory,
- eliminate runtime-specific credential shaping for third-party CLIs,
- replace OAuth volume mechanics where a runtime already depends on them,
- define every provider-specific auth flow inside this document, or
- make MoonMind a general-purpose enterprise secret-management product.

MoonMind needs a secrets system in service of orchestration, not a full replacement for dedicated secret-manager platforms.

---

## 4. Core Principles

### 4.1 References Over Raw Values

Persistent system contracts must use a reference form such as `SecretRef` rather than embedding a raw API key, refresh token, or password.

### 4.2 Local-First Baseline

The default MoonMind deployment path must remain compatible with operator-managed local infrastructure.

The baseline implementation must support a zero-`.env` startup path with smart defaults for local development and personal use.

For the default local-first managed-secret path, MoonMind should:

- generate or initialize an application master key on first start,
- store that root key in a protected local file outside the repo and outside the main application database,
- let operators override that default with a Docker secret if they already use Compose secrets, and
- allow stronger optional integrations such as OS keychains or external KMS products later.

External secret systems are optional integrations, not baseline prerequisites.

### 4.3 Encrypted Storage for Managed Secrets

Secrets that MoonMind stores on behalf of the operator must be encrypted at rest at the application layer.

Database-level disk encryption alone is not sufficient for managed secret values because it does not protect against database dumps, backups, or privileged database readers seeing plaintext fields.

### 4.4 Resolve Late, Scope Narrowly

Secret values should be resolved as late as practical and exposed to as little code as practical.

### 4.5 Proxy First

If MoonMind itself is making the provider or tool call, the preferred design is for the caller to use a MoonMind-issued capability or internal token rather than receiving the provider secret directly.

### 4.6 Fail Fast

Secret resolution failures must be explicit and actionable:

- missing secret reference,
- missing backend configuration,
- decryption failure,
- revoked backend credential,
- unsupported backend type,
- access denied, or
- expired OAuth state.

The system must not silently fall back to another secret source.

---

## 5. SecretRef Model

### 5.1 SecretRef Purpose

A `SecretRef` is the durable identifier MoonMind uses to refer to sensitive material without storing the material itself in the referencing record.

Provider Profiles, task configuration, and runtime materialization templates may point to `SecretRef` values.

### 5.2 SecretRef Shape and Validation

The `SecretRef` contract is represented as a URI string:

`Format: <backend>://<key>`

**Schema:**

- **backend**: The resolution mechanism (e.g., `env`, `db`, `exec`).
- **key**: The backend-specific locator, lookup key, or command reference.

**Validation Rules:**

1. **Format**: Must follow the `<backend>://<key>` URI pattern.
2. **Backend Allowlist**: The `backend` scheme must be one of the explicitly supported types (`env`, `db`, `exec`).
3. **Key Presence**: The `key` portion must not be empty.
4. **Backend-Specific Checks**:
   - For `db`, `key` must map to a valid stored secret ID or label (e.g., `db://providers/anthropic/default`).
   - For `env`, `key` must be a valid environment variable name (e.g., `env://ANTHROPIC_API_KEY`).
   - For `exec`, `key` must map to an allowlisted command or safe script reference (e.g., `exec://get-op-secret?ref=op://Vault/Item/Password`).
5. **No Raw Secrets**: The string must not contain embedded raw API keys, passwords, or tokens.

### 5.3 SecretRef Use Sites

Secret references may appear in:

- provider profile `secret_refs`,
- environment templates,
- generated file templates,
- runtime launch requests,
- tool auth bindings,
- scheduler-owned task configuration.

Secret references must not be replaced with resolved plaintext in persistent storage.

---

## 6. Supported Backend Classes

MoonMind must support multiple secret backend classes behind a common resolver contract.

### 6.1 `env`

Reads a secret from process or container environment.

Intended use:

- bootstrap,
- local development,
- transitional operator-managed setups.

This is convenient but not a managed encrypted-at-rest store by itself.

It should remain supported for bootstrap and break-glass cases, but it is not the preferred steady-state path for personal or production deployments once UI-managed secrets are available.

### 6.2 `db`

Stores MoonMind-managed secrets as encrypted application data in the MoonMind database.

This is the default managed-secret backend for local-first deployments.

Properties:

- secret ciphertext is stored in the database,
- encryption is performed at the application layer,
- the root key is not stored in the database,
- decryption happens only in application memory,
- UI and API flows may create and rotate these secrets.

### 6.3 `exec`

Resolves a secret by invoking an operator-approved external command or CLI.

Examples:

- `op` for 1Password,
- `bw` for Bitwarden,
- `vault` for Vault/OpenBao,
- cloud provider CLIs,
- operator-managed wrapper scripts.

This backend is the main escape hatch for integrating external secret managers without coupling the core system to one provider.

Because this backend executes local commands, it should be disabled by default and only enabled through explicit operator configuration with command allowlisting and redaction-safe diagnostics.

### 6.4 `oauth_volume`

Represents credentials or session state that live in a dedicated mounted runtime volume rather than in the managed secret store.

This is an adjacent credential-source adapter rather than a `SecretRef` backend. It shares observability and selection semantics at the Provider Profile level, but does not pass through the string-resolving `SecretRef` pipeline.

### 6.5 Future Backend Classes

Additional backend types may be added behind the same resolver boundary as long as:

- they remain reference-based in durable contracts,
- failure modes are explicit,
- observability stays consistent, and
- they do not weaken local-first baseline behavior.

---

## 7. Encryption Model

### 7.1 Managed Secret Storage

For `db`, MoonMind must encrypt secret values before persistence using authenticated encryption such as AES-GCM.

The baseline local-first root-key source is locked to a protected local key file (e.g., `/var/lib/moonmind/master.key` or `~/.moonmind/master.key`) created and managed by MoonMind outside the repo and outside the main application database.

Operators may override that default using a Docker secret (e.g., `/run/secrets/moonmind_master_key`) or another operator-managed source outside the main application database, such as:

- OS keychain,
- optional external KMS or Vault integration.

The goal is that a fresh local install can store UI-entered secrets securely without requiring the user to provision external secret infrastructure first.

### 7.2 Key Separation

The database must not contain everything needed to decrypt stored secret values on its own.

If the database is copied or dumped without the external root key source, the attacker should obtain ciphertext, metadata, and references, but not the plaintext secret values.

### 7.3 Optional Hardened Modes

MoonMind may support stronger operator-selected key custody modes, including:

- self-hosted Vault or OpenBao,
- cloud KMS,
- HSM or TPM-backed key material,
- external keychain or secret-manager products.

These are optional hardening modes, not baseline deployment requirements.

---

## 8. Resolution Lifecycle

### 8.1 Resolution Triggers

MoonMind resolves secrets only at controlled execution boundaries, including:

- provider-profile-backed runtime launch,
- MoonMind-owned outbound provider calls,
- MoonMind-owned tool or integration calls,
- explicit operator validation flows.

### 8.2 Resolution Output

The resolver returns an in-memory secret value or a narrow launch-time materialization artifact.

The resolved value must not be written back into:

- workflow payloads,
- provider profile rows,
- task definitions,
- durable run metadata,
- artifacts by default.

### 8.3 Resolution Caching

Short-lived in-memory caching may be used for performance if:

- scope is explicit,
- eviction rules are clear,
- cache lifetime is bounded,
- logs never expose raw values.

There must be no long-lived plaintext secret cache written to disk by default.

---

## 9. Execution Model

### 9.1 Proxy-First Outbound Calls

When MoonMind itself owns the provider request path, the preferred model is:

1. resolve the provider secret inside MoonMind,
2. perform the outbound call inside a MoonMind-controlled service boundary,
3. expose only MoonMind-issued capabilities or results to the caller.

This reduces the number of runtimes that ever need the raw provider secret.

### 9.2 Runtime Materialization as Escape Hatch

Some third-party runtimes require credentials in-process.

In those cases MoonMind may materialize secrets into a runtime using the narrowest feasible scope:

- per run rather than global where possible,
- per profile rather than shared broadly,
- isolated container or volume boundaries,
- removed or scrubbed when the run ends where feasible.

### 9.3 Materialization Modes

Secret materialization may occur through:

- single environment variable injection,
- environment bundles,
- generated config files,
- mounted OAuth home/volume state,
- composite strategies.

All such materialization must be launch-only and runtime-scoped.

---

## 10. Persistence Boundaries

### 10.1 Allowed Durable Secret Data

MoonMind may durably store:

- secret references,
- encrypted secret ciphertext,
- non-sensitive metadata such as labels, timestamps, backend type, and ownership,
- audit records that do not reveal the secret value.

### 10.2 Forbidden Durable Secret Data

MoonMind must not durably store raw secret values in:

- workflow histories,
- provider profile rows,
- task payloads,
- scheduler rows,
- run summaries,
- logs,
- durable generated configs,
- UI telemetry.

### 10.3 Sensitive Runtime Files

Generated config files that contain materialized credentials are sensitive runtime files.

By default, they are not durable artifacts and are not published into artifact stores.

---

## 11. Access Control and Ownership

The system must enforce explicit authorization for:

- creating managed secrets,
- rotating managed secrets,
- deleting managed secrets,
- listing secret metadata,
- binding secret refs to provider profiles,
- invoking secret resolution paths.

Operators should be able to grant visibility into metadata and usage without granting plaintext access.

The system should distinguish:

- permission to manage a secret,
- permission to bind a secret reference,
- permission to launch with a bound secret,
- permission to inspect audit events.

---

## 12. Rotation, Revocation, and Lifecycle

MoonMind must support:

- creating a managed secret,
- updating or rotating its value,
- revoking its use,
- disabling references that depend on it,
- surfacing broken references clearly.

Provider Profiles and runtime launches should fail explicitly when a referenced secret is revoked, missing, or no longer resolvable.

The system should make it possible to rotate secrets without rewriting durable provider-profile contracts when the reference identity remains the same.

### 12.1 Launch and Retry Semantics

For operator clarity, MoonMind should treat secret usage as follows:

- new launches resolve the latest currently active secret value,
- retries and restarts re-resolve rather than reusing a stale durable plaintext value,
- already-running third-party runtimes keep whatever credential material was injected at launch until they exit or are explicitly restarted, and
- revocation blocks future launches immediately but does not imply retroactive termination of already-running processes unless the runtime supports an explicit teardown path.

---

## 13. Observability and Audit

MoonMind must provide operator-visible audit and diagnostics for the secrets system without exposing plaintext secret values.

Required observability includes:

- who created, updated, rotated, or deleted a managed secret,
- which backend class a reference uses,
- which provider profiles reference a secret,
- whether a launch failed due to secret resolution,
- when a runtime required secret materialization versus proxy execution,
- redaction-safe diagnostics for support and troubleshooting.

Audit records should identify objects and events, not reveal secret contents.

---

## 14. Backup and Recovery

Encrypted managed secrets stored in the database must remain recoverable through normal backup and restore processes, provided the operator also preserves the external root key source or equivalent recovery material.

The desired-state system assumes:

- database backups contain ciphertext, not plaintext secret values,
- recovery procedures document the required key source dependencies,
- losing the root key source may render encrypted managed secrets unrecoverable.

That trade-off is acceptable and should be explicit.

---

## 15. Operator Experience

MoonMind should give operators a simple mental model:

- bootstrap secrets can come from env or an external manager,
- UI-managed secrets are encrypted in the database,
- provider profiles bind references, not values,
- MoonMind proxies secrets away from runtimes when it owns the call path,
- only runtimes that truly require credentials receive them directly.

The expected first-run onboarding flow is:

1. start MoonMind with `docker compose up -d` and no required `.env` editing. During startup, MoonMind auto-generates the local `master.key` if it does not exist.
2. open Mission Control in the browser.
3. The UI detects an unconfigured baseline and prompts the user to add a small number of secrets (e.g., a model-provider API key and a GitHub PAT).
4. Upon entry, the API stores these as `db` secrets and the UI guides the user to generate default Provider Profiles binding those secret references.
5. launch workloads successfully without external secret-manager setup.

The UI should show:

- secret name or label,
- backend type,
- whether a value is present,
- last updated time,
- reference usage,
- health or validation status,
- never the raw value after creation.

---

## 16. Relationship to Provider Profiles

Provider Profiles remain the semantic owner of:

- runtime selection,
- provider selection,
- materialization strategy,
- default model,
- launch shaping.

The Secrets System remains the semantic owner of:

- secret reference types,
- managed secret persistence,
- encryption model,
- resolution semantics,
- audit and lifecycle behavior.

Provider Profiles depend on the Secrets System for safe secret resolution, but they do not redefine it.

---

## 17. Security Requirements

The desired-state system must satisfy all of the following:

1. Raw secrets are not stored in workflow payloads or provider-profile rows.
2. UI-managed secrets are encrypted at rest at the application layer.
3. The database alone is insufficient to recover plaintext managed secrets.
4. Secret resolution is explicit, observable, and fail-fast.
5. Proxy execution is preferred when MoonMind owns the outbound call path.
6. Runtime materialization is limited to cases that genuinely require it.
7. Logs, artifacts, diagnostics, and summaries redact secret-like material.
8. Sensitive generated runtime files are treated as ephemeral by default.

---

## 18. Open Integration Points

This desired-state design intentionally leaves room for multiple integration choices, including:

- local key files,
- Docker secrets,
- OS keychains,
- 1Password,
- Bitwarden,
- Vault or OpenBao,
- cloud secret managers,
- future provider-specific auth helpers.

The contract is that all of them plug into a common reference-and-resolution model without changing the durable shapes that the rest of MoonMind depends on.
