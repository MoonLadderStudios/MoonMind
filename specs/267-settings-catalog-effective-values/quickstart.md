# Quickstart: Settings Catalog and Effective Values

## Run Focused Validation

```bash
pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q
```

## Inspect Catalog

```bash
curl -sS "http://localhost:8000/api/v1/settings/catalog?section=user-workspace&scope=workspace"
```

Expected:
- `workflow.default_task_runtime` appears with descriptor metadata.
- unexposed fields such as `workflow.github_token` do not appear.

## Inspect Effective Values

```bash
curl -sS "http://localhost:8000/api/v1/settings/effective?scope=workspace"
```

Expected:
- values include source explanations.
- SecretRef null or unresolved states appear as diagnostics, not plaintext.

## Confirm Structured Write Rejection

```bash
curl -sS -X PATCH "http://localhost:8000/api/v1/settings/workspace" \
  -H "Content-Type: application/json" \
  -d '{"changes":{"workflow.github_token":"raw-token"},"expected_versions":{},"reason":"verify MM-537"}'
```

Expected:
- response includes `error: setting_not_exposed`.
- raw secret-like values are not echoed.
