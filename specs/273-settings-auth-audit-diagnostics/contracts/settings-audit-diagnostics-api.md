# Contract: Settings Audit and Diagnostics API

## Permissions

Settings routes enforce server-side permissions:

- `settings.catalog.read`
- `settings.effective.read`
- `settings.user.write`
- `settings.workspace.write`
- `settings.system.read`
- `settings.system.write`
- `secrets.metadata.read`
- `secrets.value.write`
- `secrets.rotate`
- `secrets.disable`
- `secrets.delete`
- `provider_profiles.read`
- `provider_profiles.write`
- `operations.read`
- `operations.invoke`
- `settings.audit.read`

## GET /api/v1/settings/audit

Query:
- `key` optional setting key filter.
- `scope` optional setting scope filter.
- `limit` optional bounded result count.

Requires:
- `settings.audit.read`
- `secrets.metadata.read` to display SecretRef metadata when policy permits it.

Response item:

```json
{
  "id": "uuid",
  "event_type": "settings.override.updated",
  "key": "integrations.github.token_ref",
  "scope": "workspace",
  "actor_user_id": "uuid-or-null",
  "old_value": null,
  "new_value": null,
  "redacted": true,
  "redaction_reasons": ["descriptor_policy"],
  "reason": "operator supplied reason",
  "request_id": "request-id-or-null",
  "validation_outcome": "accepted",
  "apply_mode": "deferred",
  "affected_systems": ["github", "integrations"],
  "created_at": "timestamp"
}
```

## GET /api/v1/settings/diagnostics

Query:
- `scope` setting scope, default `workspace`.
- `key` optional setting key filter.

Requires:
- `settings.effective.read`.

Response:
- Effective source explanations.
- Read-only reasons.
- Validation/restart/readiness diagnostics.
- Recent sanitized change context when available.

## PATCH /api/v1/settings/{scope}

Behavior:
- Requires `settings.user.write` for `user`.
- Requires `settings.workspace.write` for `workspace`.
- System/operator scopes remain rejected unless explicitly supported by policy.
- Ignores client-supplied descriptor, permission, redaction, or audit metadata.
- Records audit metadata with sanitized values.
