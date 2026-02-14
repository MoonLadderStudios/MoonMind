# Phase 0: Research Findings

**Feature**: Agent Queue Artifact Upload (Milestone 2)  
**Branch**: `010-agent-queue-artifacts`  
**Date**: 2026-02-13

## Artifact Metadata Persistence

### Decision: Add dedicated `agent_job_artifacts` table

Milestone 2 will store artifact metadata in a normalized `agent_job_artifacts` table linked to `agent_jobs` by `job_id`.

### Rationale

- Source requirements explicitly allow table-backed metadata and this keeps query behavior predictable.
- Listing/download workflows need stable artifact identifiers and job scoping.
- Avoids overloading `agent_jobs.payload` with variable-length metadata arrays.

### Alternatives Considered

- Embed metadata in `agent_jobs.payload`: rejected due update complexity and reduced query ergonomics.

## Filesystem Storage Strategy

### Decision: Introduce job-scoped storage helper under queue workflow package

Use a queue-specific storage helper modeled after `moonmind/workflows/speckit_celery/storage.py` to enforce:
- relative artifact names only
- no traversal components
- resolution under `${AGENT_JOB_ARTIFACT_ROOT}/<job_id>/`

### Rationale

- Reuses proven safety pattern while avoiding coupling queue artifacts to speckit-specific paths.
- Keeps storage validation centralized and testable.

### Alternatives Considered

- Reuse speckit storage class directly: rejected because run-centric naming differs from job-centric artifact paths.

## Upload Size Enforcement

### Decision: Enforce max upload bytes in queue service before file persistence

Add config value for max upload size and reject oversized payloads before writing file or metadata.

### Rationale

- Required by Milestone 2 scope for size limits.
- Prevents oversized writes and inconsistent metadata state.

### Alternatives Considered

- Enforce only at proxy/web server layer: rejected because app-level enforcement is still needed in varied deployments.

## API Contract Decisions

### Decision: Add three endpoints to queue router

- `POST /api/queue/jobs/{jobId}/artifacts/upload`
- `GET /api/queue/jobs/{jobId}/artifacts`
- `GET /api/queue/jobs/{jobId}/artifacts/{artifactId}/download`

### Rationale

- Matches source contract exactly.
- Keeps artifact operations colocated with queue lifecycle APIs.

### Alternatives Considered

- Separate artifact router: rejected for this milestone to minimize churn.

## Validation/Test Strategy

### Decision: Add unit tests for artifact repository/router behavior and security constraints

Tests will cover:
- upload metadata persistence
- list/download happy path
- traversal rejection
- upload size rejection
- job/artifact mismatch handling

### Rationale

- Milestone 2 explicitly requires traversal and size-limit tests.
- Unit tests provide quick deterministic coverage for security-sensitive path logic.
