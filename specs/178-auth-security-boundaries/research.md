# Research: Auth Security Boundaries

## Provider-Profile Management Permission

Decision: Treat provider-profile management as an authenticated management action that requires either superuser authority for global profiles or ownership of the target OAuth/profile row for owner-scoped actions.

Rationale: Existing OAuth session endpoints already scope rows by `requested_by_user_id` and provider profile ownership. Provider profile CRUD lacked an explicit current-user dependency, so adding authorization at the router boundary gives a single, testable browser/API control surface.

Alternatives considered: Creating a new permission table was rejected because MM-335 requires boundary hardening, not a new authorization subsystem. Allowing any authenticated user was rejected because the source design explicitly requires provider-profile management permission.

## Sanitized Browser And Artifact Surfaces

Decision: Sanitize browser/API response fields and workload result artifacts at the boundary where they are serialized or written, using compact refs and redaction markers rather than raw credential-bearing values.

Rationale: The source design allows status, timestamps, failure reasons, and profile summaries, but not credential files, token values, environment dumps, or raw auth-volume listings. Boundary serialization is the last reliable point before data becomes persisted or browser-visible.

Alternatives considered: Relying only on upstream callers to avoid secrets was rejected because diagnostics and workload stdout/stderr can contain nested or provider-generated values. Removing all metadata was rejected because operators still need non-secret evidence and refs.

## Workload Auth-Volume Isolation

Decision: Keep Docker workload profiles fail-closed by default when a mount source resembles a managed-runtime auth/credential/secret volume, and require any future credential mount support to use an explicit declaration plus justification rather than ad hoc mount strings.

Rationale: Existing `WorkloadMount` validation already blocks auth-like volume names. MM-335 adds verification evidence and a contract for explicit future support without declaring a new credential-requiring workload profile in this story.

Alternatives considered: Allowing auth-like mounts when read-only was rejected because read-only still leaks credential contents. Adding a concrete credential workload profile was rejected as out of scope.

## Test Strategy

Decision: Use unit tests for router authorization and workload schema/launcher redaction, plus integration tests only if implementation touches Temporal workflow or activity invocation shapes.

Rationale: The high-risk behavior is at API and adapter boundaries that can be exercised hermetically. No workflow signature change is planned, so targeted unit tests provide the fastest and most stable evidence.

Alternatives considered: Full compose integration for all cases was rejected because it would slow iteration without adding coverage for the exact serialization boundaries under change.
