# Implementation Plan: Route Claude Auth Actions

**Branch**: `226-route-claude-auth-actions` | **Date**: 2026-04-22 | **Spec**: `specs/226-route-claude-auth-actions/spec.md`
**Input**: Single-story feature specification from `specs/226-route-claude-auth-actions/spec.md`

## Summary

Route `claude_anthropic` provider profile rows in Settings to Claude-specific auth actions while preserving existing Codex OAuth behavior. Repo inspection shows the action surface already lives in `frontend/src/components/settings/ProviderProfilesManager.tsx`, but auth capability is currently hardcoded to `profile.runtime_id === 'codex_cli'`. The planned slice extracts provider-profile auth action classification from trusted row metadata, renders Claude-specific labels for supported Claude profiles, keeps Codex OAuth session wiring unchanged, and adds focused Vitest coverage in `frontend/src/components/settings/ProviderProfilesManager.test.tsx`.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `frontend/src/components/settings/ProviderProfilesManager.tsx` renders provider rows inside Settings Provider Profiles | preserve row-level placement and add Claude action assertions | unit UI |
| FR-002 | missing | no Claude action classification exists | add disconnected Claude `Connect Claude` action | unit UI |
| FR-003 | partial | `isCodexOAuthCapable(profile)` hardcodes `runtime_id === 'codex_cli'` | replace with metadata/strategy-based action classification | unit UI |
| FR-004 | implemented_unverified | Codex OAuth tests cover Auth start/finalize/retry | keep Codex path unchanged and rerun targeted tests | unit UI |
| FR-005 | missing | no connected Claude lifecycle action rendering exists | add supported `Replace token`, `Validate`, and `Disconnect` labels from trusted metadata/readiness | unit UI |
| FR-006 | missing | Claude-specific labels are absent | render Claude labels and avoid Codex OAuth text for Claude rows | unit UI |
| FR-007 | implemented_unverified | actions render in provider row; no standalone Claude route exists | keep Claude entrypoint in row and do not add route/page | unit UI + final verify |
| FR-008 | missing | no fail-closed Claude capability logic exists | suppress Claude actions when metadata is absent or unsupported | unit UI |
| FR-009 | partial | row status shows enabled/disabled and OAuth state only | surface concise Claude readiness/validation state where available | unit UI |
| FR-010 | implemented_unverified | `spec.md` preserves MM-445 and design mappings | carry traceability through artifacts, tasks, verification, commit, and PR metadata | final verify |
| SC-001 | missing | no disconnected Claude row assertion exists | add focused UI test for `Connect Claude` and omitted generic `Auth` | unit UI |
| SC-002 | missing | no connected Claude lifecycle assertion exists | add focused UI test for supported lifecycle actions | unit UI |
| SC-003 | implemented_unverified | existing Codex OAuth tests cover current behavior | rerun/update Codex regression tests after classifier change | unit UI |
| SC-004 | missing | no assertion proves classifier avoids runtime-only Claude decisions | add unsupported/missing metadata fail-closed test | unit UI |
| SC-005 | implemented_unverified | no standalone Claude page exists today | preserve row-local flow and verify no new route/page is added | unit UI + final verify |
| SC-006 | implemented_unverified | `spec.md` and task artifacts preserve MM-445 and source mappings | carry traceability through verification | final verify |
| DESIGN-REQ-001 | implemented_unverified | provider rows are already in Settings Provider Profiles | preserve placement and readiness/status visibility | unit UI |
| DESIGN-REQ-003 | partial | Codex-only hardcoded helper is present | replace with profile metadata action classifier | unit UI |
| DESIGN-REQ-007 | missing | Claude row-level labels are absent | add Claude-specific labels and no standalone page | unit UI |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story  
**Primary Dependencies**: React, TanStack Query, existing Settings entrypoint, Vitest, Testing Library  
**Storage**: No new persistent storage; uses existing provider profile row metadata and optional command/readiness data  
**Unit Testing**: `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx` for focused iteration; final unit verification through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`  
**Integration Testing**: UI integration-style coverage in `ProviderProfilesManager.test.tsx` for row rendering and Codex OAuth request behavior; no compose-backed service integration is required for this frontend-only row-action slice  
**Target Platform**: Mission Control browser UI served by FastAPI  
**Project Type**: Web UI  
**Performance Goals**: Provider profile rows continue to render immediately without extra network calls for action classification  
**Constraints**: Do not create a standalone Claude auth page; do not change Codex OAuth session API calls; do not expose raw secrets; preserve responsive table/card layout  
**Scale/Scope**: One Settings provider profile table component and focused tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The story uses the existing Settings provider profile orchestration surface.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment prerequisites.
- III. Avoid Vendor Lock-In: PASS. Claude-specific behavior is isolated to provider-profile action classification and does not alter core orchestration.
- IV. Own Your Data: PASS. Existing provider profile metadata remains the source; no external SaaS storage is added.
- V. Skills Are First-Class and Easy to Add: PASS. No changes to executable or agent skill contracts.
- VI. Replaceable Scaffolding: PASS. Work is a narrow UI classifier and row rendering slice.
- VII. Runtime Configurability: PASS. Action availability is driven by row metadata/strategy rather than hidden constants.
- VIII. Modular Architecture: PASS. Changes stay in the Settings component and tests.
- IX. Resilient by Default: PASS. Unsupported or absent Claude metadata fails closed by hiding misleading actions.
- X. Continuous Improvement: PASS. Verification evidence is captured in MoonSpec artifacts.
- XI. Spec-Driven Development: PASS. Work proceeds from this single-story spec.
- XII. Canonical Documentation Separation: PASS. Implementation evidence stays under `specs/`; no canonical docs migration notes are added.
- XIII. Pre-Release Velocity: PASS. No compatibility aliases or old/new translation layer are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/226-route-claude-auth-actions/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── provider-profile-auth-actions.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/components/settings/
├── ProviderProfilesManager.tsx
└── ProviderProfilesManager.test.tsx

docs/tmp/jira-orchestration-inputs/
└── MM-445-moonspec-orchestration-input.md
```

**Structure Decision**: Preserve the existing Settings component boundary. Use `ProviderProfilesManager.test.tsx` for both pure row-action assertions and integration-style Codex OAuth regression coverage.

## Complexity Tracking

No constitution violations.
