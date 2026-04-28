# Implementation Plan: Provider Profile Management and Readiness in Settings

**Branch**: `run-jira-orchestrate-for-mm-541-provider-ff2c7460` | **Date**: 2026-04-28 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/271-provider-profile-readiness-settings/spec.md`

## Summary

Complete the Settings -> Providers & Secrets provider profile story for `MM-541` by adding a compact readiness contract to provider profile API responses and rendering those diagnostics in Mission Control. Current CRUD, default selection, owner authorization, SecretRef syntax validation, OAuth session actions, manual Claude API-key enrollment, and Claude-specific auth readiness exist, but readiness is not normalized for all provider profiles and the table does not expose all required launch-relevant metadata. The implementation will add backend readiness synthesis, frontend display and helpers, focused unit/UI tests, and final MoonSpec verification.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `api_service/api/routers/provider_profiles.py`; `frontend/src/components/settings/ProviderProfilesManager.tsx` expose CRUD, enable/disable, delete, default selection | preserve existing workflows and add readiness contract display | unit + UI |
| FR-002 | partial | API response includes fields; UI table shows only a subset | expose model overrides, OAuth metadata, concurrency, cooldown, tags, priority, and readiness in list/detail display | UI |
| FR-003 | partial | form exposes SecretRefs, OAuth volume fields, concurrency, cooldown, tags, priority; lacks normalized role/readiness guidance | add role-aware helper text and readiness diagnostics | UI |
| FR-004 | implemented_unverified | SecretRef syntax validation exists; UI uses JSON textarea | add tests for role labels and no plaintext display in readiness/secret refs | unit + UI |
| FR-005 | missing | Claude-specific readiness exists in `command_behavior`; no general readiness field | add `readiness` response model and synthesis from schema, required fields, SecretRefs, OAuth metadata, provider validation, enabled state, capacity, cooldown | unit + UI |
| FR-006 | missing | no generic provider-profile reference setting diagnostics found | document as out-of-band for this slice unless existing settings reference appears during implementation; add provider-profile missing/unready launch blocker model where available | unit |
| FR-007 | implemented_unverified | `docs/Security/SettingsSystem.md`; generic settings search found no inline provider profile semantics | add boundary test/evidence that settings values do not carry launch semantics | unit or final verify |
| FR-008 | implemented_unverified | runtime strategy and provider profile manager own launch payloads | preserve boundary; no generic settings launch implementation | final verify |
| FR-009 | partial | response redaction tests exist; readiness diagnostics need sanitization tests | add redaction coverage for readiness diagnostics | unit + UI |
| FR-010 | missing | new artifacts preserve `MM-541`; final verification pending | preserve issue key in tasks, verification, commit metadata if committed | final verify |
| DESIGN-REQ-002 | partial | ProviderProfile model and manager payload exist | preserve launch authority boundaries while adding display-only readiness | unit + verify |
| DESIGN-REQ-012 | partial | Providers & Secrets section exists; references not inlined in settings | add readiness display and boundary evidence | UI + verify |
| DESIGN-REQ-025 | partial | provider profile forms and Claude auth flows exist | add normalized readiness and role-aware display | unit + UI |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, React, TanStack Query, Vitest, Testing Library  
**Storage**: Existing `managed_agent_provider_profiles`, `managed_secrets`, and `provider_profile_slot_leases`; no new tables planned  
**Unit Testing**: `./tools/test_unit.sh` for final verification; targeted `pytest` and `npm run ui:test -- <path>` during iteration  
**Integration Testing**: Existing hermetic integration runner `./tools/test_integration.sh`; this story expects focused API/UI unit coverage and final unit suite unless workflow boundaries change  
**Target Platform**: Linux containers and Mission Control browser UI  
**Project Type**: Web application with FastAPI backend and React frontend  
**Performance Goals**: Provider profile list/readiness computation remains bounded to one query for profiles plus one best-effort query for managed secret metadata when needed  
**Constraints**: Never expose raw credentials, decrypted secrets, OAuth state blobs, or generated credential files; preserve runtime strategy ownership of launch semantics  
**Scale/Scope**: One Settings provider-profile story for workspace admins; no new persistent storage or new runtime launch path

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Runtime strategies and provider profile manager retain launch behavior; Settings displays and edits metadata.
- II. One-Click Agent Deployment: PASS. No new external service dependency is introduced.
- VII. Runtime Configurability: PASS. Provider profile behavior remains configuration/data driven.
- IX. Resilient by Default: PASS. Readiness diagnostics fail explicitly instead of silently falling back.
- XII. Canonical Docs vs Feature Artifacts: PASS. Implementation and rollout details stay under `specs/271-provider-profile-readiness-settings`.
- XIII. Pre-Release Compatibility Policy: PASS. New internal response fields are added directly without compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/271-provider-profile-readiness-settings/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── provider-profile-readiness-api.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/provider_profiles.py
├── db/models.py
└── services/provider_profile_service.py

frontend/src/components/settings/
├── ProviderProfilesManager.tsx
└── ProviderProfilesManager.test.tsx

tests/unit/api_service/api/routers/
└── test_provider_profiles.py
```

**Structure Decision**: Use the existing provider profile API router, Settings component, and colocated tests. Add no new package or storage layer.

## Complexity Tracking

No constitution violations require complexity exceptions.
