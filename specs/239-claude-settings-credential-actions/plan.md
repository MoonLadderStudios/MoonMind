# Implementation Plan: Claude Settings Credential Actions

**Branch**: `239-claude-settings-credential-actions` | **Date**: 2026-04-22 | **Spec**: `specs/239-claude-settings-credential-actions/spec.md`
**Input**: Single-story feature specification from `specs/239-claude-settings-credential-actions/spec.md`

## Summary

Add distinct Claude Anthropic credential method actions to the existing Settings Provider Profiles table while preserving Codex OAuth behavior. Repo inspection shows `frontend/src/components/settings/ProviderProfilesManager.tsx` already owns provider-profile row actions, Codex OAuth session startup, and Claude API-key/manual-auth submission. The current Claude action model is manual-token oriented (`Connect Claude`, `Replace token`, `Validate`, `Disconnect`) and does not expose the MM-477 labels or route `Connect with Claude OAuth` through the OAuth session lifecycle. The planned slice updates the provider-profile auth classifier and focused UI tests so `claude_anthropic` can show OAuth, API-key, validation, and disconnect actions from trusted row metadata or canonical OAuth profile shape.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `ProviderProfilesManager.tsx` renders profiles inside Settings Provider Profiles; existing tests cover table placement | preserve row-level placement | unit UI |
| FR-002 | implemented_verified | No standalone Claude auth page exists in `frontend/src/entrypoints`; existing row-local tests cover prior Claude actions | preserve no-page behavior | unit UI |
| FR-003 | partial | Claude rows can show `Connect Claude`, but not `Connect with Claude OAuth`; OAuth-capable `claude_anthropic` seed profile lacks supported UI classification | add OAuth-specific action label and classifier support | unit + integration-style UI |
| FR-004 | partial | Claude manual/API-key drawer exists behind `Connect Claude`, but the distinct `Use Anthropic API key` label is absent | add API-key action label and route to existing Managed Secrets-backed flow | unit + integration-style UI |
| FR-005 | missing | Current Claude connect action opens manual token drawer; OAuth session startup is Codex-only | route Claude OAuth action through existing OAuth session mutation | integration-style UI |
| FR-006 | partial | Manual-auth commit path exists and avoids OAuth sessions, but the API-key method is not separately selectable | expose API-key action and preserve no OAuth request behavior | integration-style UI |
| FR-007 | missing | Current labels are generic `Validate`; no OAuth-specific validation action | add metadata-driven `Validate OAuth` label | unit UI |
| FR-008 | missing | Current labels are generic `Disconnect`; no OAuth-specific disconnect action | add metadata-driven `Disconnect OAuth` label | unit UI |
| FR-009 | implemented_unverified | Unsupported metadata tests exist for manual actions; new credential-method action set needs coverage | add unsupported Claude credential-method regression | unit UI |
| FR-010 | partial | Existing tests prevent Codex `Auth` label on Claude manual rows; new OAuth/API-key labels need coverage | update labels and assertions | unit UI |
| FR-011 | implemented_unverified | Backend manual-auth commit stores Anthropic key as a Managed Secret and sets `ANTHROPIC_API_KEY` materialization | verify API-key action reaches this path | integration-style UI |
| FR-012 | implemented_unverified | Existing manual-auth tests assert no OAuth request; new `Use Anthropic API key` action needs equivalent coverage | add API-key no-OAuth regression | integration-style UI |
| FR-013 | implemented_verified | Existing Codex OAuth tests cover `/api/v1/oauth-sessions` payloads and terminal launch | keep Codex tests passing | unit + integration-style UI |
| FR-014 | missing | New artifacts preserve MM-477; final verification not yet run | carry traceability through tasks and verification | final verify |
| DESIGN-REQ-001 | implemented_verified | Settings Provider Profiles table exists and no standalone page is needed | preserve placement | unit UI |
| DESIGN-REQ-002 | partial | Existing Claude labels are not the required OAuth/API-key method labels | implement distinct method labels and metadata mapping | unit + integration-style UI |
| DESIGN-REQ-005 | partial | API-key commit path exists; row-level method selection is absent | route API-key method to Managed Secrets-backed flow and not OAuth | integration-style UI |

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
