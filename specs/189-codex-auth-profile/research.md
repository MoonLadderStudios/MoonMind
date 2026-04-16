# Research: Codex Auth Volume Profile Contract

## Implementation Boundary

Decision: Implement the story in existing Provider Profile, OAuth session, schema, and Temporal activity/workflow boundaries: `api_service/api/routers/provider_profiles.py`, `api_service/api/routers/oauth_sessions.py`, `api_service/api/schemas_oauth_sessions.py`, `api_service/services/provider_profile_service.py`, `moonmind/schemas/agent_runtime_models.py`, and OAuth session workflow/activity modules.

Rationale: The source design assigns credential refs and slot policy to Provider Profile code, interactive enrollment to OAuth terminal code, and session launch to later managed-session code. Reusing those boundaries keeps the story single-purpose and avoids inventing a parallel auth store.

Alternatives considered: Creating a new profile subsystem or compatibility wrapper was rejected because MoonMind is pre-release and internal contract values should fail fast instead of gaining hidden translation layers.

## Storage Strategy

Decision: Use existing provider profile and OAuth session persistence; do not add new tables for this story.

Rationale: The story requires preserving metadata already represented by Provider Profile records and returning secret-free snapshots. New storage would increase scope without improving the contract.

Alternatives considered: Adding a separate OAuth volume registry was rejected because the source design explicitly treats Provider Profiles as the durable metadata boundary.

## Secret Redaction Boundary

Decision: Treat profile serialization, OAuth finalization responses, workflow payload construction, logs, artifacts, and profile snapshots as secret-free boundaries that may carry compact refs and policy metadata but never credential contents, token values, auth file payloads, raw auth-volume listings, or environment dumps.

Rationale: The Jira brief and source design both make redaction observable acceptance criteria. A broad redaction boundary prevents leaks through nested provider metadata or workflow handoff payloads.

Alternatives considered: Redacting only API responses was rejected because the spec also covers workflow-visible profile snapshots, logs, and artifacts.

## Unit Test Strategy

Decision: Use focused pytest coverage through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py tests/unit/schemas/test_agent_runtime_models.py`.

Rationale: Unit tests can prove validation, serialization, redaction, profile update behavior, and payload construction without Docker, credentials, or external providers.

Alternatives considered: Provider verification tests were rejected for required validation because they require live credentials and are not part of the required PR suite.

## Integration Test Strategy

Decision: Use compose-backed hermetic integration coverage through `./tools/test_integration.sh` when Docker is available, with required coverage in `tests/integration/temporal/test_oauth_session.py`.

Rationale: The story crosses API, OAuth workflow/activity, and provider profile persistence boundaries. Hermetic integration coverage validates the real invocation shape without external credentials.

Alternatives considered: Skipping integration was rejected because workflow/activity and adapter-boundary contracts are compatibility-sensitive. Running provider verification was rejected because this story needs deterministic local evidence.

## Source Design Coverage

Decision: Preserve in-scope source coverage IDs `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-003`, `DESIGN-REQ-010`, `DESIGN-REQ-016`, and `DESIGN-REQ-020`; keep managed-session launch, auth seeding, interactive OAuth terminal UI, and workload inheritance out of scope.

Rationale: The spec is a single independently testable story focused on Provider Profile registration and redaction. Later stories can consume the profile but should not be folded into this one.

Alternatives considered: Combining downstream managed-session launch behavior was rejected because it would violate the one-story MoonSpec gate and broaden required test coverage.
