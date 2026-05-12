# Settings Validation Contract

Traceability: MM-656.

This contract describes the public Settings API and service-level validation behavior required for server-side validation and cross-setting policy enforcement.

## Validation Boundaries

Every validation result must identify one of these boundaries:

| Boundary | Meaning |
| --- | --- |
| `descriptor_generation` | Catalog descriptors are generated or filtered. |
| `write_request` | A user, workspace, system, or operator write request is received. |
| `pre_persistence` | A write is about to mutate durable settings overrides. |
| `effective_preview` | Effective values are resolved for preview before use. |
| `launch_execution` | Runtime launch consumes settings. |
| `operation_execution` | Operational commands consume settings. |
| `readiness_diagnostics` | Diagnostics report setting readiness and blockers. |

## Structured Error Shape

Settings validation failures returned by API routes must use the existing `SettingsError` envelope and include validation details.

```json
{
  "error": "invalid_setting_value",
  "message": "skills.canary_percent must be between 0 and 100.",
  "key": "skills.canary_percent",
  "scope": "workspace",
  "details": {
    "code": "numeric_constraint_failed",
    "boundary": "write_request",
    "rule": "maximum",
    "blocks": ["persistence", "preview"],
    "allowed": {"minimum": 0, "maximum": 100}
  }
}
```

Rules:
- `key` is required for setting-specific failures.
- `scope` is required when validation is scope-specific.
- `details.code` is stable enough for tests and Mission Control handling.
- `details.boundary` identifies where validation ran.
- `details.blocks` identifies the blocked target.
- SecretRef and credential-related values are redacted.

Current MM-656 validation codes:
- `descriptor_constraint_invalid`
- `enum_value_invalid`
- `feature_disabled_canary_percent`
- `invalid_secret_ref`
- `list_constraint_failed`
- `maintenance_mode_conflict`
- `max_canary_percent_exceeded`
- `numeric_constraint_failed`
- `object_constraint_failed`
- `provider_policy_denied`
- `provider_profile_disabled`
- `provider_profile_not_found`
- `publication_mode_policy_denied`
- `runtime_policy_denied`
- `secret_ref_backend_policy_denied`
- `secret_ref_unresolved`
- `string_constraint_failed`
- `type_mismatch`
- `unsafe_setting_payload`
- `unsupported_setting_type`
- `value_size_limit_exceeded`
- `dependency_not_satisfied`
- `locked_setting`
- `setting_not_exposed`
- `unsupported_scope`

## Diagnostic Shape

Settings diagnostics continue to use `SettingDiagnostic` entries, with the same stable codes and redaction rules as write errors.

```json
{
  "code": "provider_profile_disabled",
  "message": "workflow.default_provider_profile_ref references a disabled provider profile.",
  "severity": "error",
  "details": {
    "boundary": "readiness_diagnostics",
    "profile_id": "codex-default",
    "launch_blocker": true,
    "blocks": ["launch", "readiness"]
  }
}
```

## Settings API Behavior

### `PATCH /api/v1/settings/{scope}`

Request:

```json
{
  "changes": {
    "workflow.default_publish_mode": "pr",
    "skills.canary_percent": 0
  },
  "expected_versions": {
    "workflow.default_publish_mode": 1,
    "skills.canary_percent": 1
  },
  "reason": "Align workspace defaults"
}
```

Required behavior:
- Validate every changed key for exposure, scope, authorization, type, constraints, SecretRef syntax, referenced resources, dependencies, and workspace policy.
- Validate cross-setting rules using the complete submitted change set plus current effective values.
- Reject the whole request before persistence if any blocking validation error exists.
- Preserve current values when rejected.
- Return structured errors without plaintext secrets.

### `GET /api/v1/settings/effective` and `GET /api/v1/settings/effective/{key}`

Required behavior:
- Resolve effective values using existing inheritance and source metadata.
- Run `effective_preview` validation against resolved values.
- Include diagnostics for values that are missing, blocked, invalid, unresolvable, or policy-disallowed.

### `GET /api/v1/settings/diagnostics`

Required behavior:
- Run `readiness_diagnostics` validation.
- Include recent change metadata where available.
- Mark launch or operation blockers in diagnostic details.
- Redact sensitive values and SecretRef plaintext.

## Cross-Setting Rules

At minimum, the validator must reject:
- Provider profile selector referencing a missing or disabled profile.
- Canary percentage greater than zero when the associated feature is disabled.
- Default runtime outside workspace allowed runtime policy.
- SecretRef backend outside workspace allowed backend policy.
- Operational mode conflicting with maintenance policy.

## Compatibility

MoonMind is pre-release. Internal validation helpers and route mappings should be updated cleanly in one change rather than introducing compatibility aliases for superseded internal error paths. Public API response envelopes should remain coherent with the existing `SettingsError` and `SettingDiagnostic` models.
