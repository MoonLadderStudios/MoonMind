# Quickstart: Finalize OAuth from Provider Terminal

## Prerequisites

- Use the existing repository checkout.
- Use the repo-managed Python and JavaScript dependencies.
- Do not require live provider credentials for unit or hermetic integration tests.
- Keep `MOONMIND_FORCE_LOCAL_TESTS=1` for managed-agent local test mode.

## Test-First Validation Path

1. Add failing frontend tests for `frontend/src/entrypoints/oauth-terminal.test.tsx`:
   - safe session projection renders profile/runtime/provider/status/expiry/failure summary,
   - terminal attach is attempted only for attachable status plus terminal refs,
   - `Finalize Provider Profile` appears only for eligible states,
   - terminal finalize calls `POST /api/v1/oauth-sessions/{session_id}/finalize`,
   - duplicate clicks are disabled or converged,
   - success renders safe `profile_summary`,
   - failed/cancelled/expired states render allowed Cancel/Retry/Reconnect actions only when valid,
   - terminal-visible output does not render secret-like credential material.

2. Add failing Settings refresh coverage in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` or a shared query test:
   - terminal-originated finalization success invalidates or notifies Provider Profile query data,
   - existing Settings finalize and polling invalidation still pass.

3. Add failing API tests in `tests/unit/api_service/api/routers/test_oauth_sessions.py`:
   - finalize transitions through `verifying` and `registering_profile` before `succeeded`,
   - finalize response or immediate follow-up session projection includes safe `profile_summary`,
   - duplicate finalize requests in `verifying`, `registering_profile`, and `succeeded` are idempotent,
   - cancelled, expired, superseded, and unauthorized sessions do not mutate Provider Profiles,
   - terminal/request-supplied identity fields cannot override session-owned values,
   - credential-like material is not returned in response fields.

4. Add or update workflow boundary coverage when API-to-workflow signaling changes:
   - preserve `api_finalize_succeeded` compatibility for in-flight OAuth Session workflows,
   - verify no duplicate workflow-side verify/register happens after API finalization.

## Focused Commands

Frontend terminal tests:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/oauth-terminal.test.tsx
```

Frontend Settings tests:

```bash
./tools/test_unit.sh --ui-args frontend/src/components/settings/ProviderProfilesManager.test.tsx
```

Python OAuth API tests:

```bash
./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py
```

Temporal OAuth workflow boundary tests:

```bash
./tools/test_unit.sh tests/integration/temporal/test_oauth_session.py
```

Full required unit suite before finalizing implementation:

```bash
./tools/test_unit.sh
```

Hermetic integration suite before final verification when Docker is available:

```bash
./tools/test_integration.sh
```

## End-to-End Verification Scenario

1. Create or seed an OAuth Session in `awaiting_user` with safe profile metadata and durable auth refs.
2. Open `/oauth-terminal?session_id=<session_id>`.
3. Confirm the terminal page renders the safe session projection.
4. Confirm terminal attach appears only when bridge refs and attachable status are present.
5. Trigger `Finalize Provider Profile` from the terminal page.
6. Confirm the session projection shows `verifying`, then `registering_profile`, then `succeeded`.
7. Confirm the registered Provider Profile summary is visible and contains no raw credential material.
8. Confirm Settings-side Provider Profile data refreshes or is invalidated without requiring the operator to click Finalize in Settings.
9. Repeat finalization and confirm no duplicate Provider Profile is created and no different profile is mutated.
10. Repeat with cancelled, expired, superseded, and unauthorized sessions and confirm safe failure without Provider Profile mutation.

## Expected Planning Outcome

- Most requirements need code plus tests because the terminal page does not yet own finalization.
- Existing API/workflow pieces should be preserved and tightened with boundary tests.
- No new database tables or canonical docs changes are expected.
