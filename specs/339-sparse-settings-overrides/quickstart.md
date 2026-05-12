# Quickstart: Sparse Settings Override Persistence and Reset

## Scope

Validate the single `MM-654` story from `spec.md`: user/workspace overrides persist sparsely, reset restores inherited values, validation rejects unsafe or oversized values before persistence, and stale writes fail without partial changes.

## Focused Unit Iteration

Use focused tests while implementing validation gaps:

```bash
pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q
```

Expected coverage:

- Workspace override persists and reports source/version metadata.
- User override wins over workspace inheritance and intentional null is distinct from absence.
- Reset deletes only the matching override and preserves secrets plus audit rows.
- Stale expected versions return `version_conflict` and preserve prior values.
- Unsafe payload fixtures for raw secrets, OAuth sessions, decrypted credentials, generated credential config, large artifacts, workflow payloads, and operational command history are rejected.
- Oversized payload fixtures larger than 16 KiB when serialized are rejected before persistence.

## Final Unit Verification

Before completing implementation, run the repo-standard unit suite:

```bash
./tools/test_unit.sh
```

The final verification report should cite this command and the focused settings tests above.

## Hermetic Integration Strategy

Run the compose-backed integration suite when the implementation changes persistence, migrations, startup database behavior, or API contracts:

```bash
./tools/test_integration.sh
```

This suite is expected to run only `integration_ci` tests and must not require external credentials.

## Manual End-to-End Check

1. Read an eligible workspace setting with no override and confirm it reports inherited source.
2. Save a workspace override with the current expected version.
3. Read the same setting and confirm `workspace_override` source and version metadata.
4. Save a user override for an eligible user setting.
5. Read user scope and confirm `user_override` wins over workspace inheritance.
6. Reset the user override and confirm the effective source falls back to workspace or default.
7. Reset the workspace override and confirm defaults and adjacent resources remain intact.
8. Save a workspace override and user override for the same eligible key, reset only the workspace override, and confirm the user override remains readable in user scope.
9. Attempt a multi-setting write with one invalid or stale entry and confirm no requested entry is partially persisted.
10. Save a SecretRef/resource reference override and confirm only the reference is stored and no plaintext is exposed.
11. Request reset for unknown, ineligible, and already absent overrides and confirm each returns a structured outcome without deleting unrelated settings or resources.
12. Attempt stale-version, oversized, and unsafe-payload writes and confirm each fails without partial persistence.

## Traceability

Every downstream artifact and final report must preserve Jira issue key `MM-654` and the original Jira preset brief preserved in `spec.md`.
