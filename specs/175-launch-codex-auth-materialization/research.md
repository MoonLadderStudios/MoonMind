# Research: Launch Codex Auth Materialization

## Resume And Existing Implementation

Decision: Reuse the existing managed Codex adapter, managed session controller, and Codex session runtime boundaries.

Rationale: The current code already carries selected Provider Profile metadata into the launch request, conditionally mounts an auth volume at `MANAGED_AUTH_VOLUME_PATH`, seeds eligible auth files into a task-scoped Codex home, and starts Codex App Server with `CODEX_HOME` set to that path.

Alternatives considered: Introduce a new launch contract or profile materializer. Rejected because the existing boundaries match the source design and only need a targeted validation hardening.

## Auth Target Validation

Decision: Validate auth-target and Codex-home separation both at the launcher boundary and inside the session runtime before seeding credentials.

Rationale: The controller can reject unsafe launch requests before Docker starts, but the source design also requires the session runtime to validate the optional auth-volume path before materialization. Runtime validation protects direct module invocation and future launcher variants.

Alternatives considered: Rely only on controller validation. Rejected because it leaves the in-container materialization boundary weaker than the design.

## Credential Seeding

Decision: Keep one-way copy semantics and preserve the existing exclusion behavior for generated config, sessions, and runtime log databases.

Rationale: Durable OAuth credentials should seed the per-run home, while generated session state must not become the provider-profile source of truth or overwrite profile/materialized config.

Alternatives considered: Bind-mount the durable auth volume as `CODEX_HOME`. Rejected because it violates task-scoped runtime state isolation.

## Test Scope

Decision: Add focused unit coverage for adapter profile-to-launch payload and runtime validation; rely on existing controller and runtime seeding tests for the remaining source requirements.

Rationale: The changed behavior is local and testable without Docker credentials. Compose-backed integration can be run separately when Docker is available.

Alternatives considered: Add provider verification tests using real OAuth credentials. Rejected because provider verification is explicitly outside required PR verification.
