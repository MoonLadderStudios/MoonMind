# Quickstart: Claude Token Enrollment Drawer

## Focused UI Verification

```bash
npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx
```

Expected coverage:
- `Connect Claude` opens a Claude manual enrollment drawer/modal.
- The drawer shows manual enrollment lifecycle states and no terminal OAuth wording.
- Empty token submission is blocked.
- Token submission progresses through validation, saving, profile update, and ready states.
- Success and cancellation clear the token input value.
- Failure messages are redacted.
- Readiness metadata renders in the provider row status.
- Claude manual enrollment does not call `/api/v1/oauth-sessions`.

## Final Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Manual Smoke Path

1. Open Mission Control Settings.
2. Locate Providers & Secrets > Provider Profiles.
3. Use a `claude_anthropic` profile with `auth_strategy: claude_manual_token` and `auth_actions: ["connect"]`.
4. Click `Connect Claude`.
5. Confirm a focused Claude manual enrollment drawer opens.
6. Continue to the secure paste step.
7. Submit a token in a local/test environment with mocked manual-auth response.
8. Confirm ready/failure state and provider row status render without showing the submitted token.
