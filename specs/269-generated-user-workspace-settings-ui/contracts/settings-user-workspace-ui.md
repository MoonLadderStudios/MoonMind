# Contract: User / Workspace Generated Settings UI

## Catalog Read

```http
GET /api/v1/settings/catalog?section=user-workspace&scope=workspace
GET /api/v1/settings/catalog?section=user-workspace&scope=user
Accept: application/json
```

Expected response shape is `SettingsCatalogResponse`:

```json
{
  "section": "user-workspace",
  "scope": "workspace",
  "categories": {
    "Workflow": [
      {
        "key": "workflow.default_publish_mode",
        "title": "Default Publish Mode",
        "type": "enum",
        "ui": "select",
        "source": "workspace_override",
        "source_explanation": "Resolved from a workspace override.",
        "effective_value": "branch",
        "value_version": 1,
        "read_only": false,
        "applies_to": ["task_creation"]
      }
    ]
  }
}
```

## Save Changes

```http
PATCH /api/v1/settings/workspace
Content-Type: application/json
```

```json
{
  "changes": {
    "workflow.default_publish_mode": "branch"
  },
  "expected_versions": {
    "workflow.default_publish_mode": 1
  },
  "reason": "Updated from Mission Control Settings."
}
```

Rules:

- The UI sends only changed keys.
- The UI does not send raw secrets.
- Backend validation and authorization are authoritative.

## Reset Override

```http
DELETE /api/v1/settings/workspace/workflow.default_publish_mode
Accept: application/json
```

Rules:

- Show reset only for `workspace_override` or `user_override` sources.
- Refresh catalog after reset.

## UI Contract

- Scope control supports `workspace` and `user`.
- Filters include search, category, modified-only, and read-only.
- Rows show title, description, source badge, scope badge, diagnostics, affected subsystems, reload/restart badges, lock reason, reset, and generated control.
- Preview lists changed key, old value, new value, validation state, affected subsystems, and reload/restart requirements before save.
- SecretRef settings are reference inputs only; plaintext secret editing stays outside this component.
