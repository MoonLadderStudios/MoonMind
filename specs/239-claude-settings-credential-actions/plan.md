# Implementation Plan: Claude Settings Credential Actions

**Branch**: `239-claude-settings-credential-actions` | **Date**: 2026-04-22 | **Spec**: `specs/239-claude-settings-credential-actions/spec.md`
**Input**: Single-story feature specification from `specs/239-claude-settings-credential-actions/spec.md`

## Summary

Add distinct Claude Anthropic credential method actions to the existing Settings Provider Profiles table while preserving Codex OAuth behavior. Repo inspection shows `frontend/src/components/settings/ProviderProfilesManager.tsx` owns provider-profile row actions, Codex OAuth session startup, and Claude API-key/manual-auth submission. The implemented slice updates the provider-profile auth classifier, row action labels, API-key enrollment copy, and focused UI tests so `claude_anthropic` can show OAuth, API-key, validation, and disconnect actions from trusted row metadata or canonical OAuth profile shape.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `ProviderProfilesManager.tsx` renders profiles inside Settings Provider Profiles; existing tests cover table placement | preserve row-level placement | unit UI |
| FR-002 | implemented_verified | No standalone Claude auth page exists in `frontend/src/entrypoints`; existing row-local tests cover prior Claude actions | preserve no-page behavior | unit UI |
| FR-003 | implemented_verified | Claude credential-method rows expose `Connect with Claude OAuth` from trusted metadata or the canonical `claude_anthropic` OAuth profile shape | preserve OAuth-specific action label and classifier support | unit + integration-style UI |
| FR-004 | implemented_verified | Claude credential-method rows expose `Use Anthropic API key` and route it to the existing Managed Secrets-backed API-key enrollment flow | preserve API-key action label and route | unit + integration-style UI |
| FR-005 | implemented_verified | `Connect with Claude OAuth` starts the existing OAuth session mutation for `runtime_id = "claude_code"` and does not open the API-key drawer | preserve Claude OAuth action routing | integration-style UI |
| FR-006 | implemented_verified | `Use Anthropic API key` opens the API-key drawer and preserves no OAuth request behavior | preserve API-key no-OAuth behavior | integration-style UI |
| FR-007 | implemented_verified | Trusted OAuth validation metadata maps to the `Validate OAuth` row action | preserve metadata-driven `Validate OAuth` label | unit UI |
| FR-008 | implemented_verified | Trusted disconnect metadata maps to the `Disconnect OAuth` row action | preserve metadata-driven `Disconnect OAuth` label | unit UI |
| FR-009 | implemented_verified | Unsupported or metadata-free Claude rows hide misleading Claude credential-method actions | preserve unsupported-row regression coverage | unit UI |
| FR-010 | implemented_verified | Claude actions use Claude/Anthropic labels while Codex OAuth labels remain unchanged | preserve label regression coverage | unit UI |
| FR-011 | implemented_verified | Backend API-key commit stores Anthropic keys as Managed Secrets and sets `ANTHROPIC_API_KEY` materialization metadata | preserve backend API-key commit coverage | unit + integration-style UI |
| FR-012 | implemented_verified | API-key row action coverage confirms no `/api/v1/oauth-sessions` request is created | preserve API-key no-OAuth regression | integration-style UI |
| FR-013 | implemented_verified | Existing Codex OAuth tests cover `/api/v1/oauth-sessions` payloads and terminal launch | keep Codex tests passing | unit + integration-style UI |
| FR-014 | implemented_verified | MoonSpec artifacts preserve MM-477 and final verification completed against the source design mappings | carry traceability through tasks, verification, commit text, and PR metadata | final verify |
| DESIGN-REQ-001 | implemented_verified | Settings Provider Profiles table exists and no standalone page is needed | preserve placement | unit UI |
| DESIGN-REQ-002 | implemented_verified | Distinct Claude OAuth/API-key/validate/disconnect labels render from trusted metadata while preserving Codex behavior | preserve distinct method labels and metadata mapping | unit + integration-style UI |
| DESIGN-REQ-005 | implemented_verified | API-key selection routes to the Managed Secrets-backed flow and does not create an OAuth terminal session | preserve API-key routing and no-OAuth assertions | integration-style UI |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present for backend endpoints already used by this slice  
**Primary Dependencies**: React, TanStack Query, existing Settings entrypoint, existing OAuth Session API, existing provider-profile manual-auth commit API, Vitest, Testing Library  
**Storage**: No new persistent storage; uses existing provider profile row metadata and Managed Secrets-backed manual-auth API for API-key enrollment  
**Unit Testing**: `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx` for focused iteration; final unit verification through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`  
**Integration Testing**: UI integration-style component tests in `ProviderProfilesManager.test.tsx` for row rendering, OAuth session request behavior, and API-key/manual-auth no-OAuth behavior; no compose-backed service integration is required for this frontend row-action slice  
**Target Platform**: Mission Control browser UI served by FastAPI  
**Project Type**: Web UI  
**Performance Goals**: Provider profile rows continue to render without extra network calls for action classification  
**Constraints**: Do not create a standalone Claude auth page; do not expose raw secrets; keep Codex OAuth behavior unchanged; API-key enrollment must not create OAuth sessions  
**Scale/Scope**: One Settings provider profile component and focused UI tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The story uses existing provider-profile, OAuth session, and Managed Secrets-backed enrollment surfaces.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment prerequisites.
- III. Avoid Vendor Lock-In: PASS. Claude-specific behavior stays isolated to provider-profile action classification and existing adapter-specific enrollment paths.
- IV. Own Your Data: PASS. Credential material remains in existing auth volumes or Managed Secrets; UI receives only refs/metadata.
- V. Skills Are First-Class and Easy to Add: PASS. No executable or agent skill contracts are changed.
- VI. Replaceable Scaffolding: PASS. The implementation is a narrow UI action-classifier slice over existing contracts.
- VII. Runtime Configurability: PASS. Action availability is driven by provider row metadata and profile shape, not hidden global constants.
- VIII. Modular Architecture: PASS. Changes stay in the Settings component and tests.
- IX. Resilient by Default: PASS. Unsupported metadata fails closed by hiding misleading actions.
- X. Continuous Improvement: PASS. Verification evidence is captured in MoonSpec artifacts and tests.
- XI. Spec-Driven Development: PASS. Work proceeds from this single-story spec.
- XII. Canonical Documentation Separation: PASS. Implementation notes remain under `specs/` and `docs/tmp/`.
- XIII. Pre-Release Velocity: PASS. No compatibility aliases are introduced; old labels are superseded in the same action model where needed.

## Project Structure

### Documentation (this feature)

```text
specs/239-claude-settings-credential-actions/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── provider-profile-credential-actions.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/components/settings/
├── ProviderProfilesManager.tsx
└── ProviderProfilesManager.test.tsx

docs/tmp/jira-orchestration-inputs/
└── MM-477-moonspec-orchestration-input.md
```

**Structure Decision**: Preserve the existing Settings provider profile component boundary. Use the existing component test harness for both unit-like classifier assertions and integration-style row action request behavior.

## Complexity Tracking

No constitution violations.
