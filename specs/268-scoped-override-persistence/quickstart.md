# Quickstart: Scoped Override Persistence and Inheritance

## Focused Validation

Run the focused settings tests:

```bash
pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q
```

Run the full unit suite before finalizing:

```bash
./tools/test_unit.sh
```

Run hermetic integration when Docker is available:

```bash
./tools/test_integration.sh
```

## End-to-End Scenario

1. Read `/api/v1/settings/effective/workflow.default_publish_mode?scope=workspace` and confirm it inherits the configured/default value.
2. Patch `/api/v1/settings/workspace` with `workflow.default_publish_mode=branch` and matching expected version.
3. Confirm the effective value returns `branch`, source `workspace_override`, and value version `1`.
4. Patch a user-scoped SecretRef setting such as `integrations.github.token_ref` with a reference value like `env://GITHUB_TOKEN`.
5. Confirm user scope reports `user_override` while plaintext is never stored or returned.
6. Attempt a stale expected-version patch and confirm `version_conflict` with no partial persistence.
7. Delete the workspace override and confirm the inherited value returns while audit rows and adjacent resources remain.
