# Contract: Settings Overrides API

Traceability: `MM-538`, FR-001 through FR-011, DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-026.

## PATCH `/api/v1/settings/{scope}`

Persists one atomic batch of setting overrides for `scope`.

Request:

```json
{
  "changes": {
    "workflow.default_publish_mode": "branch"
  },
  "expected_versions": {
    "workflow.default_publish_mode": 1
  },
  "reason": "Use branch publishing for this workspace."
}
```

Success response:

```json
{
  "scope": "workspace",
  "values": {
    "workflow.default_publish_mode": {
      "key": "workflow.default_publish_mode",
      "scope": "workspace",
      "value": "branch",
      "source": "workspace_override",
      "source_explanation": "Resolved from a workspace override.",
      "value_version": 1,
      "diagnostics": []
    }
  }
}
```

Required failure behavior:

- `setting_not_exposed` for unknown keys.
- `invalid_scope` for unsupported scopes or keys unavailable at scope.
- `read_only_setting` for operator-locked/read-only descriptors.
- `invalid_setting_value` for type, option, constraint, raw secret, unsafe payload, or invalid reference failures.
- `version_conflict` when any expected version is stale. No changes persist.

## DELETE `/api/v1/settings/{scope}/{key}`

Deletes the matching override row for `scope` and returns the inherited effective value.

Success response:

```json
{
  "key": "workflow.default_publish_mode",
  "scope": "workspace",
  "value": "pr",
  "source": "config_or_default",
  "source_explanation": "Resolved from application settings after config and default loading.",
  "value_version": 1,
  "diagnostics": []
}
```

Required behavior:

- Deleting a missing override is idempotent and still returns the current inherited effective value.
- Reset never deletes provider profiles, managed secrets, OAuth volumes, defaults, or audit rows.
- Reset failures use the shared structured settings error shape.
