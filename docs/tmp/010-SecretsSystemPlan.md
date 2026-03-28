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
- start successfully from `docker compose up -d` without requiring `.env` edits for the baseline personal-use path,
- allow an operator to add a small number of secrets such as a provider API key and GitHub PAT through the UI after startup,
- prefer proxy-owned call paths over raw credential distribution where MoonMind owns the outbound request,
- limit runtime secret materialization to cases that truly require it.

---

## 2. Current State

Based on the current repo state:

- MoonMind primarily relies on `.env` and runtime-specific credential volumes.
- `docs/Security/ProviderProfiles.md` already assumes `secret_ref` as a credential source and launch-time resolution boundary.
- The concrete Secrets System contracts, persistence model, UI flows, and implementation boundaries are not yet established.
- `docs/Security/SecretsSystem.md` is now the canonical desired-state doc for this area.

---

## 3. Delivery Principles

Implementation should preserve the following:

- no mandatory AWS, GCP, Azure, or SaaS dependency for baseline deployment,
- no mandatory manual `.env` setup before the stack can boot locally,
- no raw secrets in workflow payloads, provider profile rows, or durable artifacts,
- fail-fast resolution with clear diagnostics,
- minimal operator friction for local-first deployments,
- explicit redaction and audit coverage.

---

## 4. Phase Overview

### Phase 0. Confirm Contracts and Scope

Status: **Complete**

Outcome:

- Shared agreement on the `SecretRef` contract, backend set, and baseline encryption/key-source approach.
- Shared agreement on the zero-`.env` startup and post-start UI onboarding path.

Tasks:

- [x] Cross-check `docs/Security/ProviderProfiles.md` so terminology matches `SecretsSystem.md`.
- [x] Lock the baseline `db` root key source for local deployments to a protected local key file created outside the repo, with Docker secret as an override path.
- [x] Decide whether `oauth_volume` remains modeled inside the secrets resolver layer or as an adjacent credential-source adapter with shared observability.
- [x] Define the initial `SecretRef` schema and validation rules.
- [x] Define the first-run onboarding UX from compose startup to secret entry in Mission Control.

Exit criteria:

- [x] Canonical docs agree on terms and boundaries.
- [x] One baseline local-first key-source approach is selected.
- [x] The no-`.env` startup expectation is explicit.
- [x] Initial resolver/backends list is frozen for v1.

### Phase 1. Persistence and Crypto Foundation

Outcome:

- MoonMind can persist managed secrets encrypted at rest using a local-first root key source external to the database.

Tasks:

- Add a secrets persistence model for managed secrets and metadata.
- Implement application-layer authenticated encryption for `db`.
- Implement root-key creation/loading from the selected baseline source.
- Ensure the database never contains everything needed to decrypt by itself.
- Add create, update, rotate, delete, and metadata-list operations at the service layer.
- Define secret state transitions such as active, disabled, rotated, deleted, or invalid.
- Add migration primitives for future import of existing `.env` values.

Validation:

- Unit tests for encrypt/decrypt behavior, tamper detection, and wrong-key failures.
- Tests proving DB ciphertext does not expose plaintext values.
- Tests proving service behavior when the root key source is missing or invalid.

### Phase 2. SecretRef Resolver Layer

Outcome:

- A single resolver contract can resolve `env`, `db`, and `exec` references at launch time.

Tasks:

- Define the `SecretRef` model and parser.
- Implement backend adapters for:
  - `env`,
  - `db`,
  - `exec`.
- Decide whether `oauth_volume` plugs into the same interface or a sibling credential-source abstraction.
- Add allowlisting and trust constraints for `exec` resolution.
- Standardize error types for missing refs, unsupported backends, access denied, and decryption failures.
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
- Make the first-run path obvious: after compose startup, the user can add a provider API key and GitHub PAT through the UI and reach a runnable state without manual secret-manager setup.
- Add authorization boundaries for secret management versus secret usage.
- Add operator-safe status surfaces showing whether a reference is healthy, missing, or broken.

Validation:

- API tests for authz and redaction.
- UI tests or deterministic manual verification for create, rotate, and delete flows.
- Tests ensuring responses never echo secret plaintext after submission.
- Deterministic manual validation for the first-run onboarding path from clean compose startup to successful task execution.

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
5. Implement resolver adapters for `env`, `db`, and `exec`.
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
- Should `exec` support arbitrary commands initially, or only operator-allowlisted integrations?
- How much in-memory caching of resolved secrets is acceptable in the first release?
- Which MoonMind-owned provider/tool path should be the first proxy-first implementation target?
- How should OAuth-volume credentials appear in audit and metadata views relative to `SecretRef`-backed secrets?

Primary risks:

- leaking resolved secrets through runtime logs or generated config files,
- overcomplicating the local-first setup path,
- leaving mixed legacy `.env` and managed-secret behavior ambiguous,
- adding secret indirection without enough boundary testing around launcher and workflow behavior.

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
