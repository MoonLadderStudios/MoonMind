# Phase 0: Research Findings

**Feature**: Manifest Task System Phase 1 (Worker Readiness)  
**Date**: March 1, 2026  
**Spec**: `specs/030-manifest-phase1/spec.md`

## Decision 1: Secret resolution should be worker-token and ownership gated

- **Decision**: Restrict manifest secret resolution to worker-token callers that advertise `manifest`, and only for jobs currently `running` and claimed by that worker.
- **Rationale**: Prevent cross-worker secret leakage and align with queue ownership controls already used for heartbeat/complete/fail flows.
- **Alternatives considered**:
  - Allow any authenticated user to resolve secrets: rejected due to over-broad exposure.
  - Allow any worker token regardless of capability: rejected because it weakens capability isolation.

## Decision 2: Use existing `manifestSecretRefs` contract as the single source for secret lookup

- **Decision**: Read profile/vault refs from `job.payload.manifestSecretRefs`; do not parse inline YAML at resolution time.
- **Rationale**: Keeps runtime deterministic and avoids re-parsing or exposing manifest content.
- **Alternatives considered**:
  - Re-parse manifest YAML from payload: rejected because payload is intentionally sanitized in API responses and may omit inline content.

## Decision 3: Resolve profile refs through AuthProviderManager with requester context

- **Decision**: Resolve each profile `envKey` via `AuthProviderManager.get_secret(provider="profile", key=...)`, passing a lightweight requester identity derived from `job.requested_by_user_id`.
- **Rationale**: Reuses existing profile->env fallback behavior and avoids adding new credential storage paths.
- **Alternatives considered**:
  - Environment-only lookup: rejected because it bypasses user profile credentials.

## Decision 4: Persist worker checkpoints via existing manifest registry columns

- **Decision**: Implement `POST /api/manifests/{name}/state` that writes `state_json`, `state_updated_at`, and optional `last_run_*` metadata through `ManifestsService`.
- **Rationale**: Phase 0 migrations already created necessary columns; callback endpoint is enough to unblock worker integration.
- **Alternatives considered**:
  - New checkpoint table: deferred until state volume or query patterns require it.

## Decision 5: Fail fast on unresolved profile refs

- **Decision**: If any requested profile ref cannot be resolved, return 422 with unresolved key names.
- **Rationale**: Workers need deterministic, explicit failures; partial secret responses can cause brittle downstream behavior.
- **Alternatives considered**:
  - Return partial results with warnings: rejected because it hides hard runtime failures.
