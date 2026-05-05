# Implementation Plan: Finalize OAuth from Provider Terminal

**Branch**: `use-this-preselected-single-story-reques-e5cfc364` | **Date**: 2026-05-05 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:d8243ed5-4171-40ec-9a44-b9251ba3d631/repo/specs/306-finalize-oauth-terminal/spec.md`

## Summary

Implement terminal-page OAuth finalization as a runtime feature. The provider terminal page must load and render a safe OAuth session projection, expose terminal attachment only when attachable, show `Finalize Provider Profile` when the session is eligible, call the same finalize operation Settings uses, render `verifying`, `registering_profile`, success, failure, cancel, retry, and reconnect states, and refresh provider-profile views after success. Repo gap analysis shows the OAuth session API, terminal attach endpoint, workflow state enum, Settings finalize mutation, and some backend tests already exist; the main gaps are terminal-page completion UI, API response/state semantics for registering-profile and idempotent duplicate finalization, Settings/terminal shared cache refresh behavior, and stronger boundary tests.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `OAuthSessionResponse` includes status/failure/profile summary fields; `oauth-terminal.tsx` only shows terminal connection status and does not render profile/runtime/provider/expiry/session summary | Add terminal-page session polling/projection and safe summary rendering | frontend unit + API unit |
| FR-002 | implemented_unverified | `oauth-terminal.tsx` waits for attachable statuses before `terminal/attach`; `oauth_sessions.py` rejects non-attachable/expired sessions; current tests cover attach behavior indirectly | Add explicit terminal UI tests for attachable vs non-attachable state and preserve API attach tests | frontend unit + API unit |
| FR-003 | missing | `oauth-terminal.tsx` has no finalize action | Add terminal-page `Finalize Provider Profile` action gated by eligible session status | frontend unit |
| FR-004 | partial | Settings calls `POST /api/v1/oauth-sessions/{session_id}/finalize`; terminal page does not | Reuse the same finalize endpoint from terminal page and shared error parsing | frontend unit + API unit |
| FR-005 | partial | Workflow signal path transitions through `verifying` and `registering_profile`; API finalize sets `succeeded` before profile registration and never persists `registering_profile` | Make API finalize publish/commit `verifying` then `registering_profile` then terminal status; preserve workflow completion signal | API unit + workflow boundary |
| FR-006 | partial | API finalize registers/updates Provider Profile, but returns `{"status": "succeeded"}` and terminal page has no success summary | Return/refresh safe `OAuthSessionResponse` with `profile_summary`; render safe registered profile summary on terminal page | API unit + frontend unit |
| FR-007 | partial | Settings invalidates provider-profile query on Settings finalize success and polling success; terminal page has no query client/cache behavior | Invalidate provider-profile query data when terminal finalization succeeds; keep Settings polling behavior | frontend unit |
| FR-008 | partial | API rejects `succeeded` finalize as invalid and allows `verifying` to run the finalize body again; tests do not cover duplicate/concurrent finalization | Make finalize idempotent for `verifying`, `registering_profile`, and `succeeded` without duplicate profile mutation | API unit |
| FR-009 | partial | Authorization is checked before verify; cancelled/expired fail by status; superseded behavior is not explicit | Add safe failures for cancelled, expired, superseded, and unauthorized sessions with no Provider Profile mutation | API unit |
| FR-010 | implemented_unverified | Terminal page has no UI controls for identity fields; API uses session row fields | Add regression tests that terminal finalization request cannot alter `profile_id`, `volume_ref`, `volume_mount_path`, runtime, or provider identity | frontend unit + API unit |
| FR-011 | partial | Settings exposes cancel/retry; terminal page does not expose Cancel, Retry, or Reconnect completion actions | Add terminal-page allowed-actions rendering for cancel/retry/reconnect where state permits | frontend unit + API unit |
| FR-012 | partial | Existing API tests check no token leakage in some paths; terminal finalization response and UI success/failure rendering are not covered | Add secret-redaction assertions for terminal-visible session projection, failure, success, logs-visible response fields, and profile summary | API unit + frontend unit |
| SCN-001 | partial | `GET /oauth-sessions/{session_id}` exists; terminal page does not render full projection | Add session projection UI and tests | frontend unit |
| SCN-002 | implemented_unverified | Attach endpoint and websocket tests exist; terminal page waits for attachable status | Add direct UI assertion for attach only when terminal refs and attachable status are present | frontend unit |
| SCN-003 | missing | No terminal finalize button | Add eligible-state button | frontend unit |
| SCN-004 | partial | Settings calls finalize endpoint; API status transitions are incomplete | Add terminal caller and API transition tests | frontend unit + API unit |
| SCN-005 | partial | Workflow native path has `registering_profile`; API finalize path does not | Add API transition and profile registration tests | API unit + workflow boundary |
| SCN-006 | partial | Backend can create/update profile; terminal has no summary | Add response profile summary and terminal summary rendering | API unit + frontend unit |
| SCN-007 | partial | Settings invalidates on its own finalize/poll success | Add terminal-success invalidation and preserve Settings behavior | frontend unit |
| SCN-008 | partial | Duplicate/succeeded finalize not idempotent | Add idempotent duplicate/concurrent finalize behavior | API unit |
| SCN-009 | partial | Unauthorized test exists; cancelled/expired/superseded terminal-safe outcomes need coverage | Add safe-failure tests | API unit |
| SC-001 | missing | Current terminal tests cover clipboard/attach only | Add UI coverage for projection and eligible finalize action | frontend unit |
| SC-002 | partial | Settings and API finalize use same endpoint; terminal does not | Add UI/API boundary coverage for shared finalize operation and state sequence | frontend unit + API unit |
| SC-003 | partial | No duplicate finalize coverage | Add duplicate/concurrent API tests | API unit |
| SC-004 | partial | Unauthorized finalize covered; cancelled/expired/superseded coverage incomplete | Add status failure matrix | API unit |
| SC-005 | partial | Settings invalidates on Settings finalize; terminal invalidation missing | Add terminal finalization invalidation test | frontend unit |
| SC-006 | partial | Some backend tests assert no token leakage; terminal rendering path missing | Add response/UI secret-safety assertions | API unit + frontend unit |
| SC-007 | implemented_verified | `spec.md` preserves source path and source mappings | Preserve traceability in plan, tasks, implementation, and verification | final verify |
| DESIGN-REQ-001 | partial | Terminal page attaches and displays connection status only | Add terminal completion UI and allowed actions | frontend unit |
| DESIGN-REQ-002 | partial | Settings endpoint exists; terminal caller and API transition sequence incomplete | Add shared endpoint caller and state sequence | frontend unit + API unit |
| DESIGN-REQ-003 | partial | Backend does not treat duplicate succeeded/registering states idempotently; terminal cannot mutate identity fields today but lacks regression tests | Add idempotency and immutable-session tests | API unit + frontend unit |
| DESIGN-REQ-004 | partial | API registers profiles but response does not expose safe profile summary for terminal success | Return/render safe profile summary | API unit + frontend unit |
| DESIGN-REQ-005 | implemented_unverified | `verify_volume_credentials` is used and tests cover some secret-safe outcomes | Add terminal-finalize response/log-facing secret-safety assertions | API unit |
| DESIGN-REQ-006 | partial | Authorization exists; browser-visible terminal completion fields are incomplete | Add safe projection fields and permission/failure coverage | API unit + frontend unit |
| DESIGN-REQ-007 | partial | Existing flow starts terminal from Settings; terminal finalization and Settings refresh missing | Complete terminal finalization and cache invalidation | frontend unit + API unit |
| DESIGN-REQ-008 | implemented_unverified | Existing auth runner/bridge/session-launch boundaries are separate | Preserve as constraints; no implementation unless tests reveal regression | final verify |

## Technical Context

**Language/Version**: Python 3.12 target per repo instructions; TypeScript/React for Mission Control OAuth terminal and Settings UI  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, React, TanStack Query, xterm.js, Vitest, pytest  
**Storage**: Existing `managed_agent_oauth_sessions` and Provider Profile rows; existing auth volumes; no new persistent tables planned  
**Unit Testing**: `./tools/test_unit.sh`; focused iteration with `pytest tests/unit/api_service/api/routers/test_oauth_sessions.py` and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/oauth-terminal.test.tsx frontend/src/components/settings/ProviderProfilesManager.test.tsx`  
**Integration Testing**: `./tools/test_integration.sh` for required hermetic `integration_ci`; targeted workflow boundary checks may use existing Temporal workflow tests even if not marked `integration_ci` when local time-skipping coverage is needed  
**Target Platform**: MoonMind API service, OAuth session workflow boundary, Mission Control Settings page, provider terminal page  
**Project Type**: Full-stack web application/runtime control-plane feature  
**Performance Goals**: Terminal session projection polling remains lightweight and responsive; duplicate finalize clicks converge without duplicate writes; no UI action blocks terminal I/O responsiveness  
**Constraints**: No raw credentials in UI responses, logs, workflow-visible outputs, or artifacts; no generic task terminal exposure; finalization must use session-owned identity and credential refs; Temporal-facing contract changes require boundary coverage or explicit compatibility notes  
**Scale/Scope**: One OAuth-session finalization story for the provider terminal page and equivalent Settings finalization behavior; auth runner startup, PTY bridge implementation, managed-session launch, and workload auth inheritance remain out of scope

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Principle I Orchestrate, Don't Recreate: PASS. Reuses existing provider CLI enrollment and OAuth session/provider-profile boundaries.
- Principle II One-Click Agent Deployment: PASS. No new external required service, secret, or persistent store.
- Principle III Avoid Vendor Lock-In: PASS. Terminal finalization remains OAuth-session/provider-profile generic with Codex-specific shape preserved as data.
- Principle IV Own Your Data: PASS. Credential material remains in operator-controlled auth volumes; only compact refs and summaries cross UI/workflow boundaries.
- Principle V Skills Are First-Class: PASS. No change to executable skill contracts or agent instruction bundles.
- Principle VI Replaceable Scaffolding With Thick Contracts: PASS. Plan centers on UI/API/workflow contracts and test-first validation.
- Principle VII Runtime Configurability: PASS. Reuses existing provider profile and OAuth session settings; no hardcoded new deployment choice.
- Principle VIII Modular Architecture: PASS. Work stays in OAuth terminal entrypoint, Settings manager, OAuth session API/schema/service, and boundary tests.
- Principle IX Resilient By Default: PASS. Idempotency, cancelled/expired/superseded failures, and workflow boundary behavior are explicit test gates.
- Principle X Continuous Improvement: PASS. Verification artifacts will report concrete outcomes and blockers.
- Principle XI Spec-Driven Development: PASS. Planning follows one `spec.md` story and produces traceable artifacts before tasks.
- Principle XII Canonical Docs vs Migration Backlog: PASS. Runtime implementation notes stay in `specs/306-finalize-oauth-terminal/`; canonical docs are source requirements only.
- Principle XIII Pre-release Compatibility Policy: PASS. No internal compatibility aliases are planned; any superseded finalize semantics will be updated cohesively.

Post-Phase 1 Re-check: PASS. Generated artifacts keep the same module boundaries, test gates, and credential-safety constraints with no justified violations.

## Project Structure

### Documentation (this feature)

```text
specs/306-finalize-oauth-terminal/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── oauth-terminal-finalization.md
└── tasks.md              # Created by /speckit.tasks, not /speckit.plan
```

### Source Code (repository root)

```text
api_service/
├── api/routers/oauth_sessions.py
├── api/schemas_oauth_sessions.py
└── services/oauth_session_service.py

moonmind/workflows/temporal/
└── workflows/oauth_session.py

frontend/src/
├── entrypoints/oauth-terminal.tsx
├── entrypoints/oauth-terminal.test.tsx
├── components/settings/ProviderProfilesManager.tsx
└── components/settings/ProviderProfilesManager.test.tsx

tests/
├── unit/api_service/api/routers/test_oauth_sessions.py
└── integration/temporal/test_oauth_session.py
```

**Structure Decision**: Use the existing full-stack MoonMind layout. Keep terminal completion UI in `frontend/src/entrypoints/oauth-terminal.tsx`, Settings cache behavior in `ProviderProfilesManager.tsx`, API/session response and finalization semantics in `api_service/api/routers/oauth_sessions.py` and `schemas_oauth_sessions.py`, and workflow compatibility assertions in existing Temporal OAuth session tests when the API-to-workflow signal contract changes.

## Complexity Tracking

No constitution violations require justification.
