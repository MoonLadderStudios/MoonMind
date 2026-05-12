# Quickstart: Effective Value Resolver With Source Explanation and Operator Locks

## Scope

Validate the single `MM-655` story from `spec.md`: effective settings values resolve in the documented source order, expose canonical source labels and explainability metadata, enforce operator locks, and return distinct diagnostics for missing, null, blocked, invalid, or unresolvable states.

## Focused Unit Iteration

Use focused tests while implementing resolver gaps:

```bash
pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q
```

Expected coverage:

- Built-in default, config-file default, environment default, workspace override, user override, provider-profile reference, SecretRef reference, and operator-lock cases return the expected source label and winning value.
- Workspace overrides shadow defaults; user overrides shadow workspace values.
- Operator locks shadow user/workspace overrides and set read-only output with a non-empty reason.
- Effective reads include source explanation, default visibility, inheritance or override state, reload/restart metadata, and affected systems.
- Diagnostic matrix covers no default, inherited null, intentionally null override, unresolvable SecretRef, missing provider profile, policy-blocked value, and post-migration invalid value.
- SecretRef and provider-profile outputs remain secret-safe and do not embed provider internals.

## Final Unit Verification

Before completing implementation, run the repo-standard unit suite:

```bash
./tools/test_unit.sh
```

The final verification report should cite this command and the focused settings tests above.

## Hermetic Integration Strategy

Run the compose-backed integration suite if implementation changes route contracts, persisted settings behavior, migrations, startup seeding, or any compose-backed Settings API behavior:

```bash
./tools/test_integration.sh
```

This suite is expected to run only `integration_ci` tests and must not require external credentials.

## Manual End-to-End Check

1. Read a setting with only a built-in default and confirm source `default`, default visibility, and no override state.
2. Read a setting supplied by config file or application settings and confirm canonical source labeling.
3. Set an environment-backed value and confirm source `environment` wins over built-in default.
4. Save a workspace override and confirm source `workspace_override` wins over defaults.
5. Save a user override and confirm source `user_override` wins over workspace.
6. Apply an operator lock and confirm source `operator_lock`, read-only output, and non-empty read-only reason for non-operator editors.
7. Select a SecretRef reference and confirm source/diagnostics are secret-safe.
8. Select a provider profile reference and confirm settings output references the profile without inlining profile internals.
9. Exercise each diagnostic category and confirm the response is distinct, actionable, and does not silently fall back.
10. Confirm `MM-655` remains preserved in MoonSpec artifacts and final verification evidence.

## Traceability

Every downstream artifact and final report must preserve Jira issue key `MM-655` and the original Jira preset brief preserved in `spec.md`.
