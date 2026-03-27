# Secrets System Plan

Status: **Draft**
Owners: MoonMind Engineering
Last Updated: 2026-03-27
Canonical Doc: [`docs/Security/SecretsSystem.md`](../Security/SecretsSystem.md)

> [!NOTE]
> This document tracks implementation phases and concrete tasks for the Secrets System.
> It is intentionally temporary and should be removed or archived once the work is complete and reflected in stable system behavior.

---

## 1. Objective

Implement the Secrets System described in [`docs/Security/SecretsSystem.md`](../Security/SecretsSystem.md) so that MoonMind can:

- use `SecretRef` as the durable contract across provider profiles and execution paths,
- store MoonMind-managed secrets encrypted at rest,
- support a local-first baseline with no required external cloud dependency,
- prefer proxy-owned call paths over raw credential distribution where MoonMind owns the outbound request,
- limit runtime secret materialization to cases that truly require it.

---

## 2. Current State

Based on the current repo state:

- MoonMind primarily relies on `.env` and runtime-specific credential volumes.
- `docs/Security/ProviderProfiles.md` already assumes `secret_ref` as a credential source and launch-time resolution boundary.
- The concrete Secrets System contracts, persistence model, UI flows, and implementation boundaries are not yet established.
- `docs/Security/SecretsAnalysis.md` contains comparative analysis but not the final desired-state contract.
- **Existing encrypted fields**: `UserProfile` stores per-user encrypted API keys (`google_api_key_encrypted`, `openai_api_key_encrypted`, `github_token_encrypted`, `anthropic_api_key_encrypted`) and `TaskRunLiveSession` stores encrypted attach/web URLs. Both use `sqlalchemy_utils.StringEncryptedType`, which defaults to AES-CBC (not authenticated encryption). These predate the Provider Profile system.
- **Existing resolution code**: `managed_api_key_resolve.py` and `moonmind/auth/secret_refs.py` implement secret resolution with two incompatible reference conventions (bare env var names and `vault://` URIs). The new `SecretRef` URI contract in the canonical doc supersedes both.

---

## 3. Delivery Principles

Implementation should preserve the following:

- no mandatory AWS, GCP, Azure, or SaaS dependency for baseline deployment,
- no raw secrets in workflow payloads, provider profile rows, or durable artifacts,
- fail-fast resolution with clear diagnostics,
- minimal operator friction for local-first deployments,
- explicit redaction and audit coverage.

---

## 4. Phase Overview

### Phase 0. Confirm Contracts and Scope

Outcome:

- Shared agreement on the `SecretRef` contract, backend set, and baseline encryption/key-source approach.

Tasks:

- Cross-check `docs/Security/ProviderProfiles.md` so terminology matches `SecretsSystem.md`.
- Decide the baseline `db_encrypted` root key source for local deployments:
  - Docker secret,
  - protected local file,
  - OS keychain,
  - or a clearly ordered fallback strategy.
- Confirm `oauth_volume` modeling as a credential source with shared observability, not a `SecretRef` resolver backend (per canonical doc Section 6.4).
- Implement the `SecretRef` URI parser and validation rules per the canonical URI scheme (Section 5.2).

Exit criteria:

- Canonical docs agree on terms and boundaries.
- One baseline local-first key-source approach is selected.
- Initial resolver/backends list is frozen for v1.

### Phase 1. Persistence and Crypto Foundation

Outcome:

- MoonMind can persist managed secrets encrypted at rest using a local-first root key source external to the database.

Tasks:

- Add a secrets persistence model for managed secrets and metadata.
- Implement AES-256-GCM authenticated encryption with envelope encryption (per-secret DEK wrapped by root KEK), replacing the `StringEncryptedType` (AES-CBC) pattern used by existing fields.
- Implement root-key loading from the selected baseline source.
- Ensure the database never contains everything needed to decrypt by itself.
- Add create, update, rotate, delete, and metadata-list operations at the service layer.
- Define secret state transitions such as active, disabled, rotated, deleted, or invalid.
- Add migration primitives for future import of existing `.env` values.

Legacy crypto migration:

- `UserProfile` encrypted API key columns (`google_api_key_encrypted`, `openai_api_key_encrypted`, `github_token_encrypted`, `anthropic_api_key_encrypted`) and `TaskRunLiveSession` encrypted URL columns currently use `StringEncryptedType` (AES-CBC). Determine their disposition:
  - Option A: Migrate these values into `db_encrypted` managed secrets and replace the columns with `SecretRef` pointers. This is the cleanest end state but requires a data migration.
  - Option B: Upgrade the column encryption in-place to AES-256-GCM while keeping the per-user column model for non-profile-bound credentials. This avoids coupling user-level keys to the managed secret lifecycle.
  - Option C: Leave as-is for now if these fields are on a deprecation path (replaced by provider-profile-bound secrets). Document the gap explicitly.
- Whichever option is chosen, the existing `ENCRYPTION_MASTER_KEY` env var and `api_service/core/encryption.py` must be reconciled with the new root key source so operators are not managing two separate key hierarchies.

Validation:

- Unit tests for encrypt/decrypt behavior, tamper detection, and wrong-key failures.
- Tests proving DB ciphertext does not expose plaintext values.
- Tests proving service behavior when the root key source is missing or invalid.

### Phase 2. SecretRef Resolver Layer

Outcome:

- A single resolver contract can resolve `env`, `db_encrypted`, and `exec` references at launch time.

Tasks:

- Implement the `SecretRef` URI parser per canonical doc Section 5.2 (scheme-based dispatch to backend adapters).
- Implement backend adapters for:
  - `env`,
  - `db_encrypted`,
  - `exec`.
- Implement `exec` backend security envelope: operator allowlist file, direct exec (no shell), bounded timeout, stdout-only output contract, stderr redaction (per canonical doc Section 6.3).
- Unify the existing resolution code in `managed_api_key_resolve.py` and `moonmind/auth/secret_refs.py` into the new resolver layer. The existing `vault://` support should map to either an `exec` adapter wrapping the Vault CLI or a dedicated `vault` convenience backend, depending on the decision in Phase 0.
- `oauth_volume` does not participate in the resolver — confirm that Provider Profile launch paths route `oauth_volume` credential sources through the materialization pipeline directly.
- Standardize error types for missing refs, unsupported backends, access denied, resolution timeout, and decryption failures.
- Add redaction-safe tracing and metrics around resolution attempts.

Validation:

- Unit tests per backend.
- Tests for invalid references and fail-fast behavior.
- Tests ensuring resolved values never leak into structured logs or returned metadata.

### Phase 3. Provider Profile Integration

Outcome:

- Provider Profiles can bind secret references and resolve them during launch without storing raw values.

Tasks:

- Wire `SecretRef` validation into provider profile create/update paths.
- Ensure profile persistence stores refs and templates only.
- Integrate resolution into the runtime materialization pipeline described in `ProviderProfiles.md`.
- Enforce the launch-time ordering of clear-env, secret resolution, file generation, and runtime shaping.
- Ensure generated config files containing secrets are treated as sensitive runtime files.
- Update profile examples and docs to use the final `SecretRef` shape.

Validation:

- Boundary tests covering provider profile load -> secret resolution -> runtime materialization.
- Tests proving workflow payloads and persisted profile rows do not contain raw secrets.
- Compatibility checks for any in-flight or persisted provider-profile payload shapes affected by the contract changes.

### Phase 4. UI and API Surfaces

Outcome:

- Operators can manage MoonMind-stored secrets safely through controlled API and UI flows.

Tasks:

- Add API endpoints for:
  - create or update managed secret,
  - list metadata,
  - rotate,
  - disable,
  - delete,
  - validate bindings or usage references.
- Add UI screens or forms showing metadata and usage status without ever re-displaying secret values.
- Add authorization boundaries for secret management versus secret usage.
- Add operator-safe status surfaces showing whether a reference is healthy, missing, or broken.

Validation:

- API tests for authz and redaction.
- UI tests or deterministic manual verification for create, rotate, and delete flows.
- Tests ensuring responses never echo secret plaintext after submission.

### Phase 5. Proxy-First Execution Paths

Outcome:

- MoonMind-owned outbound integrations prefer internal secret use over passing provider credentials into runtimes.

Tasks:

- Inventory provider and tool call paths that MoonMind owns directly.
- Identify which call paths can switch to proxy-first execution now.
- Introduce internal capability or token patterns where a caller needs MoonMind authorization instead of the provider secret.
- Keep runtime credential materialization only for third-party executables that genuinely require it.
- Document which runtimes remain escape-hatch cases and why.

Validation:

- Integration tests for at least one MoonMind-owned provider call path proving the runtime does not receive the provider secret.
- Tests showing failure modes when proxy credentials are invalid or revoked.

### Phase 6. Runtime Escape-Hatch Hardening

Outcome:

- Third-party runtime launches that require secrets receive the minimum materialized credential scope and leave minimal residue.

Tasks:

- Scope credential injection to per-run or per-profile boundaries where possible.
- Clear conflicting env vars before launch.
- Scrub or delete generated sensitive runtime files after use where feasible.
- Audit runtime volumes, temp files, and artifacts for residual leakage paths.
- Tighten log redaction around runtime startup diagnostics and subprocess failures.

Validation:

- Runtime-boundary tests for env injection and file materialization.
- Tests ensuring sensitive runtime files are not published as durable artifacts by default.
- Manual verification for at least one CLI runtime that requires direct credentials.

### Phase 7. Migration and Operator Rollout

Outcome:

- Existing operators can move from `.env`-only usage to the new Secrets System without ambiguous mixed-state behavior.

Tasks:

- Provide an import path from `.env` into managed encrypted secrets where appropriate.
- Decide how legacy `.env` values interact with `SecretRef`-based profiles during transition.
- Update quickstart, operator docs, and examples.
- Add diagnostics that point operators toward the preferred secret path.
- Remove or de-emphasize obsolete docs once the new path is ready.

Validation:

- Migration tests covering import of `.env` values.
- Tests covering missing/misaligned references after migration.
- Deterministic operator walkthrough from fresh clone to configured provider profile.

---

## 5. Cross-Cutting Work Items

These tasks span multiple phases and should be tracked continuously:

- Redaction review for logs, artifacts, summaries, and diagnostics.
- Threat-model review for DB dumps, backups, temp files, and subprocess env leakage.
- Documentation sync between `SecretsSystem.md`, `ProviderProfiles.md`, and operator-facing setup docs.
- Boundary-level testing for workflow, launcher, and provider-profile contracts.
- Review of secret-like data already written into existing artifact or summary paths.

---

## 6. Initial Task Breakdown

The following backlog is a practical first slice for implementation:

1. Finalize `SecretRef` schema and backend names.
2. Choose baseline local root-key source.
3. Add encrypted secret persistence table and service.
4. Implement crypto wrapper and key-loader abstraction.
5. Implement resolver adapters for `env`, `db_encrypted`, and `exec`.
6. Integrate resolver into provider-profile launch materialization.
7. Add redaction-safe audit events and diagnostics.
8. Add API and UI support for managed secrets.
9. Convert at least one provider-profile flow to the new managed path.
10. Convert at least one MoonMind-owned outbound call path to proxy-first execution.
11. Add `.env` import and migration guidance.
12. Update quickstart and security documentation.

---

## 7. Risks and Decisions to Resolve

Open decisions:

- Which baseline root-key source provides the best local UX without weakening the security model too much?
- Which MoonMind-owned provider/tool path should be the first proxy-first implementation target?
- How should the existing `vault://` support in `secret_refs.py` be mapped into the new resolver — as a dedicated backend or as an `exec` adapter wrapping the Vault CLI?
- What is the disposition of the existing `UserProfile` per-user encrypted API key columns? (See Phase 1 options A/B/C.)
- How should the existing `ENCRYPTION_MASTER_KEY` env var be reconciled with the new root key source?

Resolved decisions (now in canonical doc):

- `exec` backend requires an operator-managed allowlist; arbitrary commands are not supported (Section 6.3).
- `oauth_volume` is a credential source with shared observability, not a `SecretRef` resolver backend (Section 6.4).
- Resolution caching is bounded to a single task run lifetime with no cross-run sharing (Section 8.3).
- `SecretRef` uses a URI scheme for backend dispatch (Section 5.2).
- References resolve to always-latest; version pinning is an explicit extension (Section 5.4).

Primary risks:

- leaking resolved secrets through runtime logs or generated config files,
- overcomplicating the local-first setup path,
- leaving mixed legacy `.env` and managed-secret behavior ambiguous,
- adding secret indirection without enough boundary testing around launcher and workflow behavior,
- managing two separate encryption key hierarchies (existing `ENCRYPTION_MASTER_KEY` and new root key source) during transition.

---

## 8. Completion Criteria

This plan is complete when all of the following are true:

- `SecretRef` is the durable secret contract used by provider profiles and related execution paths.
- MoonMind-managed secrets are encrypted at rest at the application layer.
- Baseline local deployments do not require an external public-cloud secret service.
- Proxy-first execution is used for MoonMind-owned call paths where feasible.
- Third-party runtime secret materialization is treated as a narrow, explicit escape hatch.
- Operators can manage secrets through safe API/UI flows.
- Redaction and audit behavior are verified.
- Canonical docs describe the stable system, and this tmp plan can be archived or deleted.
