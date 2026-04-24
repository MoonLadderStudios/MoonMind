# Implementation Plan: Route Claude Auth Actions

**Branch**: `226-route-claude-auth-actions` | **Date**: 2026-04-22 | **Spec**: `specs/226-route-claude-auth-actions/spec.md`
**Input**: Single-story feature specification from `specs/226-route-claude-auth-actions/spec.md`

## Summary

Route `claude_anthropic` provider profile rows in Settings to Claude-specific auth actions while preserving existing Codex OAuth behavior. Repo inspection shows the action surface already lives in `frontend/src/components/settings/ProviderProfilesManager.tsx`, but auth capability is currently hardcoded to `profile.runtime_id === 'codex_cli'`. The planned slice extracts provider-profile auth action classification from trusted row metadata, renders Claude-specific labels for supported Claude profiles, keeps Codex OAuth session wiring unchanged, and adds focused Vitest coverage in `frontend/src/components/settings/ProviderProfilesManager.test.tsx`.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | verified | `ProviderProfilesManager.tsx` continues rendering actions inside Settings Provider Profiles; T018-T020 passed | preserve row-level placement | unit UI |
| FR-002 | verified | disconnected Claude fixture renders `Connect Claude`; T004 and T017 passed | maintain disconnected Claude action | unit UI |
| FR-003 | verified | provider-profile auth classifier derives actions from metadata/strategy instead of a Codex-only helper; T006 and T011 passed | maintain metadata/strategy-based action classification | unit UI |
| FR-004 | verified | Codex OAuth regression assertions remained passing through T009, T016, T017, and T020 | keep Codex path unchanged | unit UI |
| FR-005 | verified | connected Claude fixture renders supported `Replace token`, `Validate`, and `Disconnect` actions; T005 and T017 passed | maintain supported lifecycle labels | unit UI |
| FR-006 | verified | Claude rows omit generic Codex `Auth` while rendering Claude labels; T004-T005 and T017 passed | maintain Claude-specific labels | unit UI |
| FR-007 | verified | Claude actions remain row-local and no standalone Claude auth route/page was added; T012, T018, and T019 passed | keep Claude entrypoint in row | unit UI + final verify |
| FR-008 | verified | unsupported or missing Claude metadata hides lifecycle actions; T006 and T014 passed | maintain fail-closed unsupported metadata behavior | unit UI |
| FR-009 | verified | Claude metadata-backed status labels render in the Status cell; T007 and T015 passed | maintain secret-free status label rendering | unit UI |
| FR-010 | verified | `spec.md`, `plan.md`, `tasks.md`, and verification artifacts preserve MM-445 and source mappings | carry traceability through verification, commit, and PR metadata | final verify |
| SC-001 | verified | disconnected Claude row assertion covers `Connect Claude` and omitted generic `Auth`; T004 and T017 passed | maintain focused UI test | unit UI |
| SC-002 | verified | connected Claude lifecycle assertion covers supported actions; T005 and T017 passed | maintain focused UI test | unit UI |
| SC-003 | verified | Codex OAuth behavior remains covered by existing regression assertions; T009, T016, and T017 passed | maintain Codex regression tests | unit UI |
| SC-004 | verified | unsupported metadata assertion proves action decisions are not runtime-only; T006 and T011 passed | maintain fail-closed test | unit UI |
| SC-005 | verified | row-local flow and absence of standalone Claude auth route/page are covered by T018-T019 | maintain row-local verification | unit UI + final verify |
| SC-006 | verified | MM-445 and DESIGN-REQ-001/003/007 remain present in spec, plan, tasks, and final verification work | maintain traceability | final verify |
| DESIGN-REQ-001 | verified | provider rows remain in Settings Provider Profiles with readiness/status visibility; T015 and T018 passed | maintain placement and status visibility | unit UI |
| DESIGN-REQ-003 | verified | Codex-only hardcoded helper was replaced by provider metadata action classification; T011 passed | maintain metadata classifier | unit UI |
| DESIGN-REQ-007 | verified | Claude row-level labels render without a standalone page; T012-T019 passed | maintain Claude labels and row-local flow | unit UI |

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

└── MM-445-moonspec-orchestration-input.md
```

**Structure Decision**: Preserve the existing Settings component boundary. Use `ProviderProfilesManager.test.tsx` for both pure row-action assertions and integration-style Codex OAuth regression coverage.

## Complexity Tracking

No constitution violations.
