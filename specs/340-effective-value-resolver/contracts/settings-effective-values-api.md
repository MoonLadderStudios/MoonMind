# Contract: Settings Effective Values API

## Scope

This contract covers the API-visible Settings effective-value and diagnostics behavior needed for `MM-655`. It refines existing Settings routes rather than introducing a new public subsystem.

## Effective Value Read

```http
GET /api/v1/settings/effective/{key}?scope={scope}
```

Returns one fully explained effective setting value for the requested scope.

Required response fields:

- `key`
- `scope`
- `value`
- `source`
- `source_explanation`
- `default_value`
- `inheritance_state`
- `read_only`
- `read_only_reason`
- `apply_mode`
- `activation_state`
- `active`
- `pending_value`
- `affected_process_or_worker`
- `completion_guidance`
- `value_version`
- `diagnostics`

Source vocabulary:

- `default`
- `config_file`
- `environment`
- `workspace_override`
- `user_override`
- `provider_profile`
- `secret_ref`
- `operator_lock`

Behavior:

- Workspace overrides win over defaults.
- User overrides win over workspace overrides for user scope.
- Operator locks win over user and workspace overrides.
- SecretRef and provider profile values are reported as references and diagnostics only.
- Missing, blocked, invalid, and unresolvable states return explicit diagnostics rather than silently falling back.

## Effective Values List

```http
GET /api/v1/settings/effective?scope={scope}
```

Returns all effective values available at the requested scope using the same value shape as the single-value endpoint.

## Settings Diagnostics

```http
GET /api/v1/settings/diagnostics?scope={scope}&key={key}
```

Returns diagnostic-focused effective values. It must preserve the same source vocabulary and diagnostic categories as effective reads and may include recent change context when available.

## Error and Safety Requirements

- Unknown keys return the existing structured settings error contract.
- Invalid scopes return the existing structured settings error contract.
- Locked settings must be read-only for non-operator editors and include a read-only reason.
- Secret plaintext, decrypted credentials, auth tokens, and provider-profile internals must not appear in responses.
- Policy-blocked or invalid winning candidates must produce actionable diagnostics.

## Test Requirements

- Service-level tests cover source precedence and diagnostic categories.
- API-level tests cover response shape, source vocabulary, lock read-only behavior, and redaction.
- Existing tests that assert superseded internal labels must be updated in the same change rather than preserving aliases.
