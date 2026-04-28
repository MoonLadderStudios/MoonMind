# Implementation Plan: Generated User and Workspace Settings UI

**Branch**: `269-generated-user-workspace-settings-ui`
**Date**: 2026-04-28
**Spec**: [spec.md](./spec.md)
**Input**: Single-story runtime spec from MM-539 Jira preset brief.

## Summary

Implement the missing generated User / Workspace Settings UI on top of the existing backend Settings API. The backend already exposes catalog, effective value, patch, and reset routes with descriptor metadata and server-side validation, so the primary delivery work is a React renderer that fetches descriptors by scope, renders descriptor-driven controls, tracks local intent, previews pending changes, saves changed keys with expected versions, resets overrides, and surfaces read-only, source, diagnostics, reload, and SecretRef states. Validation will use focused frontend unit tests for UI behavior plus existing backend route/service tests for API boundaries.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `api_service/api/routers/settings.py`, `api_service/services/settings_catalog.py`; frontend placeholder in `frontend/src/entrypoints/settings.tsx` | Add frontend catalog fetch and descriptor grouping for User / Workspace | frontend unit + API integration evidence |
| FR-002 | partial | Backend descriptors support enum, integer, boolean, secret_ref; frontend renderer absent | Add generated controls for supported descriptor types, with list/key-value fallback handling | frontend unit |
| FR-003 | partial | Backend descriptor fields exist; frontend does not display them in User / Workspace | Render row metadata, badges, diagnostics, reload indicators, affected subsystems, lock reason, reset | frontend unit |
| FR-004 | missing | No User / Workspace filtering UI | Add search, category, scope, modified-only, and read-only filters | frontend unit |
| FR-005 | partial | Backend `PATCH /settings/{scope}` persists overrides with expected versions | Track pending edits and submit changed keys only with expected versions | frontend unit + existing backend tests |
| FR-006 | missing | No change preview in frontend | Add preview panel for changed keys, values, validation, affected subsystems, reload/restart | frontend unit |
| FR-007 | partial | Backend reset route exists; frontend does not expose reset in User / Workspace | Add discard and reset-to-inherited actions | frontend unit + existing backend tests |
| FR-008 | partial | Backend descriptor supports read-only; frontend does not render locked rows | Disable editing and show lock reasons from descriptors | frontend unit |
| FR-009 | partial | Backend rejects unsafe secret-like values; frontend generic SecretRef UI absent | Render SecretRef as reference input/picker-style control and avoid plaintext secret copy | frontend unit |
| FR-010 | partial | Backend returns structured errors; frontend needs sanitized display | Display API errors without dumping response payload secrets | frontend unit |
| FR-011 | implemented_verified | MM-539 and canonical brief preserved in `spec.md` | Preserve in downstream artifacts and final verification | traceability review |
| SCN-001 | partial | Settings page has sections, but User / Workspace is placeholder | Generated workspace descriptor view | frontend unit |
| SCN-002 | missing | No scope switch in User / Workspace | Fetch by selected scope | frontend unit |
| SCN-003 | missing | No pending change preview | Add pending changes and preview | frontend unit |
| SCN-004 | partial | Backend read-only metadata exists | Render disabled controls and lock reason | frontend unit |
| SCN-005 | partial | Backend reset exists | Add reset button and refresh | frontend unit |
| SCN-006 | partial | Backend SecretRef descriptor exists | Add SecretRef reference control and tests | frontend unit |
| DESIGN-REQ-001 | partial | Backend catalog and scoped overrides exist; UI missing | Add UI over existing backend | frontend unit + backend tests |
| DESIGN-REQ-004 | partial | Backend catalog section `user-workspace`; UI placeholder | Render catalog-driven section | frontend unit |
| DESIGN-REQ-009 | partial | Descriptor metadata and server validation exist; UI missing controls/preview | Add UI controls and metadata display | frontend unit |
| DESIGN-REQ-023 | partial | Backend inheritance and reset behavior exist; UI missing reset/source display | Add source badges and reset-to-inherited | frontend unit |

## Technical Context

- **Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present for existing Settings API.
- **Primary Dependencies**: React, TanStack Query, existing FastAPI Settings routes, existing SQLAlchemy-backed Settings override service, Vitest and Testing Library.
- **Storage**: Existing `settings_overrides` and `settings_audit_events` tables; no new persistent storage.
- **Unit Testing**: Vitest/Testing Library for frontend; pytest for existing backend services.
- **Integration Testing**: Existing FastAPI route tests for Settings API and focused frontend integration-style component tests with mocked fetch.
- **Target Platform**: Mission Control browser UI served by MoonMind API.
- **Project Type**: Web application plus existing API service.
- **Performance Goals**: Descriptor filtering must remain client-local after one catalog fetch per scope; save/reset refreshes the affected scope.
- **Constraints**: Frontend must not decide backend eligibility, validation, sensitivity, or authorization. SecretRef settings must not request or display plaintext secrets. Generic UI remains scoped to User / Workspace; Providers & Secrets and Operations stay specialized.
- **Scale/Scope**: One generated renderer for current descriptor catalog, designed to handle additional eligible descriptors without bespoke forms.

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Work uses existing Settings API and Mission Control surfaces.
- **II. One-Click Agent Deployment**: PASS. No new external dependency or setup requirement.
- **III. Avoid Vendor Lock-In**: PASS. Generic settings metadata is internal and provider-neutral; SecretRefs remain references.
- **IV. Own Your Data**: PASS. Settings and overrides remain operator-controlled.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime changes.
- **VI. Replaceable Scaffolding**: PASS. Renderer uses descriptor contracts rather than bespoke forms.
- **VII. Powerful Runtime Configurability**: PASS. Feature exposes runtime configuration through backend metadata.
- **VIII. Modular and Extensible Architecture**: PASS. Adds a frontend component at Settings UI boundary.
- **IX. Resilient by Default**: PASS. Errors are surfaced and backend validation remains authoritative.
- **X. Facilitate Continuous Improvement**: PASS. UI makes validation and affected subsystems visible.
- **XI. Spec-Driven Development**: PASS. Spec, plan, and tasks precede implementation.
- **XII. Documentation Separation**: PASS. Runtime work remains in feature artifacts; canonical docs are read as source requirements.
- **XIII. Pre-Release Velocity**: PASS. No compatibility aliases or legacy paths are introduced.

## Project Structure

```text
frontend/src/entrypoints/settings.tsx
frontend/src/components/settings/GeneratedSettingsSection.tsx
frontend/src/components/settings/GeneratedSettingsSection.test.tsx
api_service/api/routers/settings.py
api_service/services/settings_catalog.py
tests/unit/api_service/api/routers/test_settings_api.py
tests/unit/services/test_settings_catalog.py
specs/269-generated-user-workspace-settings-ui/
```

## Phase 0 Research

See [research.md](./research.md).

## Phase 1 Design

See [data-model.md](./data-model.md), [contracts/settings-user-workspace-ui.md](./contracts/settings-user-workspace-ui.md), and [quickstart.md](./quickstart.md).

## Complexity Tracking

No constitution violations or added complexity exceptions.
