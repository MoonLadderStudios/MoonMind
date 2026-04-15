# Contract: Auth Security Boundaries

## Provider Profile Management API

Management endpoints under `/api/v1/provider-profiles` must require the current user to have provider-profile management permission.

Allowed management behavior:

- Superusers may list, create, update, and delete global provider profiles.
- A profile owner may read or mutate only their own owner-scoped profile.
- Other authenticated users receive `403 Forbidden`.

Response contract:

- Responses may include compact refs such as `profile_id`, `runtime_id`, `credential_source`, `runtime_materialization_mode`, `volume_ref`, and `volume_mount_path`.
- Responses must not include raw credential contents, token values, private key blocks, cookies, raw auth-volume listings, or environment dumps.

## OAuth Session API

Endpoints under `/api/v1/oauth-sessions` must preserve owner scoping and sanitized responses.

Allowed response fields:

- Session ID, runtime ID, profile ID, lifecycle status, terminal refs, transport, timestamps, and sanitized failure reason.

Forbidden response fields:

- Credential files, token values, environment dumps, raw auth-volume listings, or secret-like values from verification internals.

## Docker Workload Boundary

Workload profiles and launch requests must not implicitly inherit managed-runtime auth volumes.

Required behavior:

- Auth-like Docker named volumes are rejected unless future support adds an explicit workload credential declaration with non-empty justification.
- Workload runtime stdout/stderr, diagnostics, metadata, and artifact publication outputs must be sanitized before they are persisted or returned.
- Diagnostics may include environment override keys, but not environment override values.

## Verification Contract

MM-335 verification must prove:

- No secret fixture values appear in workflow/API/workload outputs.
- Unauthorized provider-profile/OAuth management actions are rejected.
- Undeclared workload auth-volume mounts fail closed.
- Source coverage IDs `DESIGN-REQ-008`, `DESIGN-REQ-009`, `DESIGN-REQ-017`, `DESIGN-REQ-018`, `DESIGN-REQ-019`, `DESIGN-REQ-021`, and `DESIGN-REQ-022` are covered by tests or explicit evidence.
