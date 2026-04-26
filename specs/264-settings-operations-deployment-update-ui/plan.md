# Implementation Plan: Settings Operations Deployment Update UI

**Branch**: `264-settings-operations-deployment-update-ui` | **Date**: 2026-04-26 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story runtime feature specification from `specs/264-settings-operations-deployment-update-ui/spec.md`, generated from Jira issue `MM-522`.

## Summary

Add the missing Mission Control Settings Operations Deployment Update card on top of the existing deployment operation API endpoints. The implementation will extend `OperationsSettingsSection` to fetch stack state and image targets, render current deployment evidence, provide target/mode/options/reason controls, require browser confirmation before POSTing to the typed update endpoint, and render recent action fields when provided. Verification is frontend-first with focused React/Vitest coverage plus existing backend route tests for the typed deployment endpoint.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `OperationsSettingsSection.tsx`; focused UI test asserts card under Operations and no deployment navigation | complete | Vitest passed |
| FR-002 | implemented_verified | UI fetches/renders stack state, configured image, running evidence, health, and last run | complete | Vitest passed |
| FR-003 | implemented_verified | UI fetches image targets and exposes repository/reference controls without runner image controls | complete | Vitest passed |
| FR-004 | implemented_verified | `preferredReference()` chooses digest/recent tag before mutable tags | complete | Vitest passed |
| FR-005 | implemented_verified | mutable reference warning renders for `latest`/`stable` | complete | Vitest passed |
| FR-006 | implemented_verified | mode defaults to `changed_services`; force recreate offered only from allowed mode data/fallback with warning | complete | Vitest passed |
| FR-007 | implemented_verified | frontend reason guard and backend validation remain present | complete | Vitest passed |
| FR-008 | implemented_verified | confirmation text includes current image, target, mode, stack, services, mutable warning, and restart warning | complete | Vitest passed |
| FR-009 | implemented_verified | UI posts typed payload to `/api/v1/operations/deployment/update` | complete | Vitest passed |
| FR-010 | implemented_verified | UI renders last update and optional recent action fields defensively | complete | Vitest passed |
| FR-011 | implemented_verified | raw command-log link renders only when explicitly permitted and present | complete | Vitest passed |
| FR-012 | implemented_verified | MM-522 preserved in artifacts and verification | complete | rg passed |
| DESIGN-REQ-001 | implemented_verified | Settings Operations placement and no top-level deployment nav covered | complete | Vitest passed |
| DESIGN-REQ-002 | implemented_verified | Current deployment, target, mode/options, reason, and confirmation covered | complete | Vitest passed |
| DESIGN-REQ-016 | implemented_verified | target image controls exist; updater runner controls absent | complete | Vitest passed |
| DESIGN-REQ-017 | implemented_verified | concise recent action fields and gated log links covered | complete | Vitest passed |
| SC-001 | implemented_verified | focused UI test | complete | Vitest passed |
| SC-002 | implemented_verified | focused UI test | complete | Vitest passed |
| SC-003 | implemented_verified | focused UI test | complete | Vitest passed |
| SC-004 | implemented_verified | focused UI test | complete | Vitest passed |
| SC-005 | implemented_verified | traceability grep | complete | rg passed |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but backend endpoints already exist  
**Primary Dependencies**: React, TanStack Query, Zod, Vitest, Testing Library, existing FastAPI deployment endpoints  
**Storage**: No new persistent storage  
**Testing**: `npm run ui:test -- frontend/src/components/settings/OperationsSettingsSection.test.tsx`; final `./tools/test_unit.sh` when feasible  
**Target Platform**: Mission Control browser UI  
**Project Type**: Web frontend over existing FastAPI backend  
**Performance Goals**: Load deployment state and targets without blocking worker pause controls; keep polling bounded to existing query defaults  
**Constraints**: No top-level navigation; no updater runner image control; no raw logs link unless explicitly allowed  
**Scale/Scope**: One Settings Operations card for the allowlisted `moonmind` stack

## Test Strategy

**Unit test strategy**: Use focused React/Vitest coverage in `frontend/src/components/settings/OperationsSettingsSection.test.tsx` for pure UI decisions and component-local behavior: release-tag defaulting, mutable-tag warnings, mode availability, reason validation, confirmation text construction, typed request payload construction, absence of updater runner controls, and raw command-log link gating.

**Integration test strategy**: Use the same component test as a browser-facing integration boundary by rendering `OperationsSettingsSection` with TanStack Query and mocked `fetch` responses for the real deployment operation endpoints: `GET /api/v1/operations/deployment/stacks/{stack}`, `GET /api/v1/operations/deployment/image-targets`, and `POST /api/v1/operations/deployment/update`. Run it through the repository unit runner with `./tools/test_unit.sh --ui-args frontend/src/components/settings/OperationsSettingsSection.test.tsx` so Python unit coverage and targeted frontend behavior are verified together.

**Existing backend contract evidence**: Keep `tests/unit/api/routers/test_deployment_operations.py` as the backend route contract evidence for authorization, policy validation, state shape, image target shape, and typed submission queueing. This story does not require new backend integration tests because it consumes already-tested endpoints without changing their contract.

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Uses existing typed deployment operation endpoints.
- II. One-Click Agent Deployment: PASS. Builds on Docker Compose deployment update controls.
- III. Avoid Vendor Lock-In: PASS. UI uses MoonMind's typed operation API, not provider-specific calls.
- IV. Own Your Data: PASS. Reads operator-controlled deployment state.
- V. Skills First-Class: PASS. Does not redefine executable skill contracts.
- VI. Replaceable Scaffolding: PASS. UI stays thin over existing contracts and tests.
- VII. Runtime Configurability: PASS. Uses API-provided policy/target data instead of hardcoded runner controls.
- VIII. Modular Architecture: PASS. Scope limited to Settings Operations component.
- IX. Resilient by Default: PASS. Confirmation and fail-closed backend validation remain in place.
- X. Continuous Improvement: PASS. Verification artifacts and traceability are produced.
- XI. Spec-Driven Development: PASS. This plan follows `spec.md`.
- XII. Canonical Docs Separation: PASS. No canonical doc migration checklist is added.
- XIII. Pre-release Compatibility: PASS. No compatibility aliases or internal contract transforms.

## Project Structure

```text
frontend/src/components/settings/OperationsSettingsSection.tsx
frontend/src/components/settings/OperationsSettingsSection.test.tsx
specs/264-settings-operations-deployment-update-ui/
```

## Complexity Tracking

No constitution violations.
