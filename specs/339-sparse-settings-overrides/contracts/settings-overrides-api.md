# Contract: Settings Override API

This contract describes the existing public settings surfaces relevant to `MM-654`.

## Read Effective Setting

```http
GET /api/v1/settings/effective/{key}?scope={scope}
```

Parameters:

- `key`: setting key.
- `scope`: `user` or `workspace` for this story.

Successful response:

```json
{
  "key": "workflow.default_publish_mode",
  "scope": "workspace",
  "value": "branch",
  "source": "workspace_override",
  "source_explanation": "Resolved from a workspace override.",
  "value_version": 1,
  "diagnostics": []
}
```

Required behavior:

- If no override exists, return the inherited effective value and inherited source.
- If an override exists, return the override value, source, and version metadata.
- Do not reveal secret plaintext in values, diagnostics, or errors.

## Save Overrides

```http
PATCH /api/v1/settings/{scope}
Content-Type: application/json
```

Request:

```json
{
  "changes": {
    "workflow.default_publish_mode": "branch"
  },
  "expected_versions": {
    "workflow.default_publish_mode": 1
  },
  "reason": "Use branch publishing for workspace tasks."
}
```

Successful response:

```json
{
  "scope": "workspace",
  "values": {
    "workflow.default_publish_mode": {
      "key": "workflow.default_publish_mode",
      "scope": "workspace",
      "value": "branch",
      "source": "workspace_override",
      "value_version": 1,
      "diagnostics": []
    }
  }
}
```

Failure responses:

- `400 invalid_setting_value`: value is larger than 16 KiB when serialized, off-schema, or contains unsafe payload data.
- `400 invalid_scope`: setting is not editable at the requested scope.
- `404 setting_not_exposed`: setting key is not exposed.
- `409 version_conflict`: expected version does not match current version; no partial changes are persisted.
- `423 read_only_setting`: setting is exposed but not writable.

Required behavior:

- Validate every change before persistence.
- Reject the whole batch if any change is invalid or stale.
- Store SecretRef/resource references as references only.
- Never store raw secret plaintext, OAuth session blobs, decrypted credentials, generated secret-bearing config, large artifacts, workflow payloads, or operational command history.

## Reset Override

```http
DELETE /api/v1/settings/{scope}/{key}
```

Successful response:

```json
{
  "key": "workflow.default_publish_mode",
  "scope": "workspace",
  "value": "none",
  "source": "config_or_default",
  "value_version": 1,
  "diagnostics": []
}
```

Required behavior:

- Delete only the matching override.
- Return the inherited effective value after reset.
- Preserve defaults, provider profiles, managed secrets, OAuth credential volumes, and settings audit history.
- Return structured settings outcomes or errors for unknown, ineligible, read-only, already absent, or invalid-scope requests without deleting unrelated resources.
