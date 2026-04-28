# Contract: Settings Catalog and Effective Values

## `GET /api/v1/settings/catalog`

Query parameters:
- `section`: optional `providers-secrets`, `user-workspace`, or `operations`
- `scope`: optional `user`, `workspace`, `system`, or `operator`

Response:
```json
{
  "section": "user-workspace",
  "scope": "workspace",
  "categories": {
    "Workflow": [
      {
        "key": "workflow.default_task_runtime",
        "title": "Default Task Runtime",
        "description": "Runtime used when a task does not explicitly request one.",
        "category": "Workflow",
        "section": "user-workspace",
        "type": "enum",
        "ui": "select",
        "scopes": ["workspace"],
        "default_value": "codex",
        "effective_value": "codex",
        "override_value": null,
        "source": "config_or_default",
        "source_explanation": "Resolved from application settings after config and default loading.",
        "options": [{"value": "codex", "label": "Codex"}],
        "constraints": null,
        "sensitive": false,
        "secret_role": null,
        "read_only": true,
        "read_only_reason": "Scoped override persistence is not enabled for this story.",
        "requires_reload": false,
        "requires_worker_restart": false,
        "requires_process_restart": false,
        "applies_to": ["task_creation", "workflow_runtime"],
        "depends_on": [],
        "order": 10,
        "audit": {"store_old_value": true, "store_new_value": true, "redact": false},
        "value_version": 1,
        "diagnostics": []
      }
    ]
  }
}
```

## `GET /api/v1/settings/effective`

Query parameters:
- `scope`: required by behavior, defaults to `workspace`

Response:
```json
{
  "scope": "workspace",
  "values": {
    "workflow.default_publish_mode": {
      "key": "workflow.default_publish_mode",
      "scope": "workspace",
      "value": "pr",
      "source": "config_or_default",
      "source_explanation": "Resolved from application settings after config and default loading.",
      "value_version": 1,
      "diagnostics": []
    }
  }
}
```

## `GET /api/v1/settings/effective/{key}`

Returns one `EffectiveSettingValue` or a structured settings error.

## `PATCH /api/v1/settings/{scope}`

Request:
```json
{
  "changes": {
    "workflow.github_token": "raw-token"
  },
  "expected_versions": {},
  "reason": "attempt to mutate an unexposed field"
}
```

`MM-537` does not implement scoped override persistence. The route exists to reject unsupported writes with structured settings errors.

Error response:
```json
{
  "error": "setting_not_exposed",
  "message": "Setting workflow.github_token is not exposed through the Settings API.",
  "key": "workflow.github_token",
  "scope": "workspace",
  "details": {}
}
```
