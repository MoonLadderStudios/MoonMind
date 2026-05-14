# Contract: Settings HTTP API Surface

Traceability: `MM-657`, FR-001 through FR-017, DESIGN-REQ-001 through DESIGN-REQ-013.

## Shared Error Envelope

Every settings API failure returns:

```json
{
  "error": "invalid_setting_value",
  "message": "A user-actionable message.",
  "key": "workflow.default_publish_mode",
  "scope": "workspace",
  "details": {
    "code": "enum_value_invalid",
    "boundary": "write_request",
    "rule": "enum",
    "blocks": ["persistence"]
  }
}
```

Rules:
- `error` is the public route-level code.
- `details.code` may carry the lower-level validation code.
- `key` and `scope` are present when the failure is setting-specific.
- Secret plaintext, raw credentials, tokens, and unsafe payload fragments are never returned.

## GET `/api/v1/settings/catalog`

Query:
- `section`: optional `providers-secrets`, `user-workspace`, or `operations`.
- `scope`: optional `user`, `workspace`, `system`, or `operator`.

Success:
- Returns descriptors grouped into categories.
- Descriptors include effective values, source explanations, version metadata, read-only metadata, reload metadata, dependency metadata, and audit policy.
- Only explicitly exposed settings are returned.

Failures:
- Unknown section: structured settings error.
- Unknown scope: structured settings error.
- Missing permission: `permission_denied`.

## GET `/api/v1/settings/effective`

Query:
- `scope`: setting scope, default `workspace`.

Success:
- Returns all effective settings available at the requested scope.
- Each value includes source explanation, diagnostics, value version, read-only metadata, and reload/application metadata where available.

Failures:
- Unknown scope: structured settings error.
- Missing permission: `permission_denied`.

## GET `/api/v1/settings/effective/{key}`

Query:
- `scope`: setting scope, default `workspace`.

Success:
- Returns one effective setting value for the key and scope.

Failures:
- Unknown key: structured settings error.
- Key unavailable at scope: structured settings error.
- Missing permission: `permission_denied`.

## PATCH `/api/v1/settings/{user|workspace}`

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

Success:
- Persists all accepted changes atomically.
- Returns refreshed effective values or descriptors for affected settings.
- Records audit-visible metadata.

Failures:
- `setting_not_exposed` for unknown or non-exposed keys.
- Structured scope error for unsupported scopes or key/scope mismatches.
- `read_only_setting` for read-only or operator-locked settings.
- `invalid_setting_value` for type, constraint, reference, dependency, policy, or unsafe-payload failures.
- `version_conflict` when any expected version is stale; no change persists.
- `permission_denied` when caller lacks write permission.

## DELETE `/api/v1/settings/{user|workspace}/{key}`

Success:
- Deletes the matching override if present.
- Returns the inherited effective value.
- Does not delete managed secrets, provider profiles, OAuth volumes, defaults, or audit rows.

Failures:
- Same key/scope/read-only/permission envelope as write requests.

## POST `/api/v1/settings/validate`

Request:

```json
{
  "scope": "workspace",
  "changes": {
    "skills.canary_percent": 25
  },
  "expected_versions": {
    "skills.canary_percent": 1
  }
}
```

Success:
- Evaluates proposed changes without committing them.
- Returns whether changes are accepted and any validation issues grouped by key.
- Uses the same validation rules as write requests.
- Returns no raw secret plaintext.

Required no-commit behavior:
- No `settings_overrides` rows are inserted or updated.
- No settings audit mutation is recorded for validation-only requests.

## POST `/api/v1/settings/preview`

Request:

```json
{
  "scope": "workspace",
  "changes": {
    "workflow.default_publish_mode": "branch"
  },
  "expected_versions": {
    "workflow.default_publish_mode": 1
  }
}
```

Success:
- Evaluates proposed changes without committing them.
- Returns proposed effective-value diffs.
- Returns dependency warnings and reload/restart requirements.
- Returns validation issues for blocking changes.
- Redacts sensitive values in diffs and diagnostics.

Required no-commit behavior:
- No `settings_overrides` rows are inserted or updated.
- No settings audit mutation is recorded for preview-only requests.

## GET `/api/v1/settings/audit`

Query:
- `key`: optional setting key filter.
- `scope`: optional scope filter.
- `limit`: bounded result count.

Success:
- Returns audit records filtered by key and/or scope.
- Redacts values according to descriptor audit policy.
- Preserves non-sensitive metadata such as event type, actor, reason, validation outcome, apply mode, affected systems, and redaction status.

Failures:
- Unknown scope: structured settings error.
- Missing permission: `permission_denied`.
