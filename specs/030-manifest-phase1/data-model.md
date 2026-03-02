# Data Model: Manifest Task System Phase 1 (Worker Readiness)

**Feature**: Manifest Task System Phase 1 (Worker Readiness)  
**Date**: March 1, 2026  
**Spec**: `specs/030-manifest-phase1/spec.md`

## Entities

### 1. ManifestSecretResolutionRequest

Worker-request payload accepted by `POST /api/queue/jobs/{jobId}/manifest/secrets`.

- `includeProfile` (bool, default `true`): whether to resolve profile/env-backed refs.
- `includeVault` (bool, default `true`): whether to return vault ref metadata.

### 2. ManifestSecretProfileValue

One resolved profile/env-backed secret returned to the worker.

- `provider` (optional string)
- `field` (optional string)
- `envKey` (string)
- `normalized` (optional string)
- `value` (string, resolved secret)

### 3. ManifestSecretVaultValue

One vault reference returned unchanged for worker-side Vault resolution.

- `mount` (optional string)
- `path` (optional string)
- `field` (optional string)
- `ref` (string)

### 4. ManifestSecretResolutionResponse

Envelope returned by manifest secret resolution endpoint.

- `profile` (list of `ManifestSecretProfileValue`)
- `vault` (list of `ManifestSecretVaultValue`)

### 5. ManifestStateUpdateRequest

Callback payload accepted by `POST /api/manifests/{name}/state`.

- `stateJson` (object, required)
- `lastRunJobId` (optional UUID)
- `lastRunStatus` (optional string)
- `lastRunStartedAt` (optional datetime)
- `lastRunFinishedAt` (optional datetime)

### 6. ManifestRecord (existing table, updated fields)

Existing `manifest` table row updated by this phase.

- `state_json` (jsonb / mutable dict)
- `state_updated_at` (timestamp)
- `last_run_job_id` (UUID, optional)
- `last_run_status` (string, optional)
- `last_run_started_at` (timestamp, optional)
- `last_run_finished_at` (timestamp, optional)
- `updated_at` (timestamp)

## State Transitions

### Secret Resolution Guard

`queued` or `failed` jobs -> rejected for secret resolution.  
`running` + claimed by calling worker -> eligible for secret resolution.

### Manifest State Callback

- On valid callback, `state_json` and `state_updated_at` are overwritten with latest values.
- Optional last-run metadata fields are updated only when supplied.
- `updated_at` is always refreshed on successful callback.
