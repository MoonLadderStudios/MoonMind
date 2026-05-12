# Data Model: Server-Side Validation and Cross-Setting Policy Enforcement

Traceability: MM-656; supports `specs/341-server-side-validation/spec.md`.

## Setting Descriptor

Represents one backend-owned catalog entry.

Fields relevant to this story:
- `key`: stable setting key.
- `section`: settings page section.
- `scopes`: scopes where writes are allowed.
- `value_type`: descriptor value category: boolean, string, number/integer, enum, list, object, or SecretRef.
- `options`: allowed enum values when applicable.
- `constraints`: numeric, string, list, object, and size restrictions.
- `depends_on`: same-setting or cross-setting dependencies.
- `read_only` / `read_only_reason`: whether ordinary writes are blocked.
- `operator_locked_value` / `operator_lock_reason`: operator-enforced value that wins over ordinary overrides.
- `audit`: redaction and audit visibility policy.

Validation rules:
- Keys must be explicitly registered and exposed by the backend catalog.
- Values must match `value_type` and all descriptor constraints.
- Client-supplied descriptor metadata is ignored.

## Setting Change

Represents a requested setting mutation or preview candidate.

Fields:
- `key`: target setting key.
- `scope`: user, workspace, system, or operator.
- `value`: proposed value.
- `expected_version`: optional optimistic concurrency guard.
- `actor`: authenticated subject and permissions.
- `boundary`: validation timing boundary where the change is evaluated.

Validation rules:
- Unknown keys, unsupported scopes, unauthorized actors, locked settings, invalid values, invalid references, and workspace-policy violations block the change.
- Rejected changes must not mutate persisted overrides.
- Errors must identify key, scope, rule/code, boundary, and message.

## Workspace Policy

Represents constraints that limit allowed values or combinations within a workspace.

Fields:
- `allowed_runtimes`.
- `allowed_providers`.
- `maximum_canary_percent`.
- `allowed_publication_modes`.
- `allowed_secret_ref_backends`.
- `maintenance_mode` and allowed operation modes.
- `feature_enabled` flags relevant to canary or runtime settings.

Validation rules:
- A setting value or setting combination is invalid when it violates any applicable policy field.
- Policy failures are blocking for writes, previews, launches, operations, and readiness diagnostics when the target boundary would consume the invalid configuration.

## Referenced Resource

Represents a resource named by a setting value.

Types:
- Provider profile.
- SecretRef.
- Runtime identifier.
- Publication mode or target.
- Operation mode.

Validation rules:
- Required references must exist and be enabled/allowed before the setting can be used.
- SecretRef validation must validate syntax and backend policy without exposing referenced plaintext.
- Missing or disabled provider profiles produce launch-blocking structured errors.

## Validation Result

Represents the outcome of validating one setting or cross-setting combination.

Fields:
- `accepted`: boolean.
- `key`: setting key when the result is setting-specific.
- `scope`: validation scope when applicable.
- `boundary`: one of `descriptor_generation`, `write_request`, `pre_persistence`, `effective_preview`, `launch_execution`, `operation_execution`, `readiness_diagnostics`.
- `code`: stable machine-readable rule code.
- `message`: actionable human-readable explanation.
- `details`: redacted structured metadata, such as allowed values, policy name, reference type, and launch blocker flag.
- `blocks`: list of blocked targets such as persistence, preview, launch, operation, or readiness.

State transitions:
- Proposed -> Accepted: all validation rules pass.
- Proposed -> Rejected: one or more blocking validation results are produced.
- Persisted -> Diagnostic error: existing durable value becomes invalid after policy, resource, or migration state changes.
- Persisted -> Blocked for launch/operation: value remains visible but cannot be used by the consuming boundary.
