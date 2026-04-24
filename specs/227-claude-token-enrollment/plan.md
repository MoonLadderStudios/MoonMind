# Implementation Plan: Claude Token Enrollment Drawer

**Branch**: `227-claude-token-enrollment` | **Date**: 2026-04-22 | **Spec**: `specs/227-claude-token-enrollment/spec.md`
**Input**: Single-story feature specification from `specs/227-claude-token-enrollment/spec.md`

## Summary

Add a focused Claude manual token enrollment drawer to the existing Settings Provider Profiles table. Repo inspection shows the adjacent MM-445 story already routes supported `claude_anthropic` rows to Claude-specific action labels in `frontend/src/components/settings/ProviderProfilesManager.tsx`, but those actions currently only publish a notice and no drawer lifecycle exists. The implementation will keep Codex OAuth behavior unchanged, open a Claude-specific drawer from trusted row actions, model the manual enrollment lifecycle states, submit through a narrow secret-free manual-auth request boundary, clear token state on success/cancel, redact failure text, and extend focused Vitest coverage in `ProviderProfilesManager.test.tsx`.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | Claude row actions exist in `ProviderProfilesManager.tsx`; no drawer/modal exists | add row-local enrollment drawer/modal | unit UI + integration-style UI |
| FR-002 | missing | no enrollment lifecycle state model exists | add explicit drawer state labels and transitions | unit UI |
| FR-003 | implemented_unverified | Claude buttons avoid `Auth`; no drawer content exists to verify wording | add regression assertions for no terminal OAuth wording | unit UI |
| FR-004 | missing | no token paste input exists | add secure token input in drawer | unit UI |
| FR-005 | missing | no success path or token state exists | clear token after successful manual-auth commit | unit UI |
| FR-006 | missing | no cancel/close token state exists | clear token on drawer cancel/close | unit UI |
| FR-007 | missing | no validation/save/profile-update progress UI exists | add progress states around manual-auth submission | unit UI |
| FR-008 | missing | no Claude validation failure UI exists | redact failure messages before rendering | unit UI |
| FR-009 | partial | `auth_status_label` renders; structured connected state is not surfaced | render trusted readiness metadata details | unit UI |
| FR-010 | partial | `auth_status_label` renders; timestamp/secret/readiness/failure details absent | render structured readiness metadata details | unit UI |
| FR-011 | implemented_unverified | Claude buttons do not call Codex OAuth today; drawer path not covered | preserve no Codex OAuth request on Claude enrollment | integration-style UI |
| FR-012 | missing | no token submission form exists | block empty token submission | unit UI |
| FR-013 | implemented_unverified | `spec.md` preserves MM-446; downstream artifacts still need traceability | preserve MM-446 through tasks and verification | final verify |
| SC-001 | missing | no drawer/modal tests | add open-drawer and no OAuth wording test | unit UI |
| SC-002 | missing | no lifecycle tests | add external-step/token/progress/ready test | unit UI |
| SC-003 | missing | no local token state tests | add success and cancel clearing tests | unit UI |
| SC-004 | missing | no redaction tests | add failure redaction test | unit UI |
| SC-005 | partial | status label exists only as one string | add structured readiness metadata rendering tests | unit UI |
| SC-006 | implemented_unverified | Codex OAuth tests exist; Claude drawer no-call regression absent | assert Claude manual flow does not hit `/api/v1/oauth-sessions` | integration-style UI |
| SC-007 | implemented_unverified | `spec.md` preserves traceability | preserve in plan, tasks, verification | final verify |
| DESIGN-REQ-005 | partial | Claude manual strategy metadata exists; drawer/manual submission path missing | add manual enrollment UI path, not terminal OAuth | unit UI + integration-style UI |
| DESIGN-REQ-008 | missing | no drawer lifecycle exists | add drawer states and secure token flow | unit UI |
| DESIGN-REQ-009 | partial | plain status label exists | add readiness metadata and redacted failure rendering | unit UI |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected in this story 
**Primary Dependencies**: React, TanStack Query, existing Settings entrypoint, Vitest, Testing Library 
**Storage**: No new persistent storage in this story; token persistence is represented by a manual-auth request boundary that returns secret-free readiness metadata 
**Unit Testing**: `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx` for focused iteration; final unit verification through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` 
**Integration Testing**: UI integration-style coverage in `ProviderProfilesManager.test.tsx` for drawer interaction, mocked manual-auth request shape, and Codex OAuth no-call regression; no compose-backed service integration is required for this frontend-focused story 
**Target Platform**: Mission Control browser UI served by FastAPI 
**Project Type**: Web UI 
**Performance Goals**: Provider profile rows render without extra requests; manual-auth request occurs only after explicit token submission 
**Constraints**: Do not expose token values in status, errors, notices, logs, or artifacts; do not describe Claude enrollment as terminal OAuth; do not invoke Codex OAuth session APIs for Claude; preserve existing Codex behavior 
**Scale/Scope**: One Settings provider profile table component, focused tests, and MoonSpec artifacts

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The story uses the existing provider profile Settings orchestration surface.
- II. One-Click Agent Deployment: PASS. No new service, storage, or deployment prerequisite.
- III. Avoid Vendor Lock-In: PASS. Claude-specific enrollment stays isolated behind provider-profile metadata and a manual-auth boundary.
- IV. Own Your Data: PASS. Token values are submitted only through a controlled boundary and are not persisted in UI state after completion or cancellation.
- V. Skills Are First-Class and Easy to Add: PASS. No executable skill contract changes.
- VI. Replaceable Scaffolding: PASS. Work is a narrow UI state machine around an existing Settings component.
- VII. Runtime Configurability: PASS. Claude support remains driven by profile metadata/strategy.
- VIII. Modular Architecture: PASS. Changes stay in the Settings component and tests.
- IX. Resilient by Default: PASS. Empty token submission is blocked and failure output is redacted.
- X. Continuous Improvement: PASS. Evidence is captured in MoonSpec artifacts and tests.
- XI. Spec-Driven Development: PASS. Implementation proceeds from this single-story spec.
- XII. Canonical Documentation Separation: PASS. Planning and migration notes remain under `specs/`/`local-only handoffs`; canonical docs are not rewritten for this frontend slice.
- XIII. Pre-Release Velocity: PASS. No compatibility aliases or old/new behavior shims are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/227-claude-token-enrollment/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── claude-manual-enrollment.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/components/settings/
├── ProviderProfilesManager.tsx
└── ProviderProfilesManager.test.tsx

└── MM-446-moonspec-orchestration-input.md
```

**Structure Decision**: Preserve the existing Settings component boundary. Use `ProviderProfilesManager.test.tsx` for both unit-style state assertions and integration-style request/no-Codex-OAuth regression coverage.

## Complexity Tracking

No constitution violations.
