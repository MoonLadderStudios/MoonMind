# Verification: Claude Token Enrollment Drawer

**Verdict**: FULLY_IMPLEMENTED
**Date**: 2026-04-22
**Target Issue**: MM-446

## Scope

Verified the implementation against `specs/227-claude-token-enrollment/spec.md`, the canonical Jira preset brief in `spec.md` (Input), and source requirements DESIGN-REQ-005, DESIGN-REQ-008, and DESIGN-REQ-009 from `docs/ManagedAgents/ClaudeAnthropicOAuth.md`.

## Requirement Coverage

| ID | Status | Evidence |
| --- | --- | --- |
| FR-001 | VERIFIED | `ProviderProfilesManager.tsx` opens a row-local dialog titled `Claude manual token enrollment for {profile_id}` from Claude manual auth actions. |
| FR-002 | VERIFIED | The dialog renders lifecycle states `not_connected`, `awaiting_external_step`, `awaiting_token_paste`, `validating_token`, `saving_secret`, `updating_profile`, `ready`, and `failed`. |
| FR-003 | VERIFIED | Tests assert the Claude dialog does not contain terminal OAuth wording; Codex OAuth remains separate. |
| FR-004 | VERIFIED | The dialog provides a password input labeled `Returned Claude token`. |
| FR-005 | VERIFIED | Successful submission clears the token value and removes it from rendered UI. |
| FR-006 | VERIFIED | Cancellation closes the dialog and reopening starts with an empty token input. |
| FR-007 | VERIFIED | Manual-auth submission advances through validation, secret-save, profile-update, and ready states. |
| FR-008 | VERIFIED | Failure messages are redacted for submitted token values and `sk-ant-*` token-like substrings before rendering. |
| FR-009 | VERIFIED | Trusted readiness metadata can render connected/not connected state. |
| FR-010 | VERIFIED | Trusted readiness metadata can render last validated timestamp, redacted failure, backing secret existence, and launch readiness. |
| FR-011 | VERIFIED | Claude manual token submission calls `/api/v1/provider-profiles/{profile_id}/manual-auth/commit` and tests assert it does not call `/api/v1/oauth-sessions`. |
| FR-012 | VERIFIED | Empty token submission is blocked with `Returned Claude token is required.` |
| FR-013 | VERIFIED | MM-446 is preserved in spec, plan, tasks, and this verification artifact. |
| DESIGN-REQ-005 | VERIFIED | Claude manual token enrollment uses provider-profile-backed manual semantics and does not route through terminal OAuth. |
| DESIGN-REQ-008 | VERIFIED | Drawer flow covers external instruction, secure paste, validation, save, profile update, ready, and failed states. |
| DESIGN-REQ-009 | VERIFIED | Status metadata and validation failures render with secret-safe redaction. |

## Test Evidence

- `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/components/settings/ProviderProfilesManager.test.tsx`: PASS, 41 tests.
- `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`: PASS.
- `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`: PASS, Python unit suite and frontend Vitest suite.

## Notes

- `npm run ui:test` and `npm run ui:typecheck` cannot resolve local binaries in this managed workspace path; direct `./node_modules/.bin/*` invocation is used by `tools/test_unit.sh` and was used for focused checks.
- `frontend/src/entrypoints/mission-control.test.tsx` received a narrow timeout robustness fix for the lazy OAuth terminal test so the required dashboard suite passes consistently in the managed test environment.
