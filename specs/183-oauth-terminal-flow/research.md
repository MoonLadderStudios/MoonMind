# Research: OAuth Terminal Enrollment Flow

## Implementation Boundary

Decision: Implement first-party OAuth terminal session, auth runner, and PTY/WebSocket bridge lifecycle in existing MoonMind modules: api_service/api/routers/oauth_sessions.py, moonmind/workflows/temporal/runtime/terminal_bridge.py, moonmind/workflows/temporal/workflows/oauth_session.py, frontend/src/entrypoints/mission-control.tsx.
Rationale: The source design requires thin orchestration boundaries and explicit ownership rather than new parallel runtime systems.
Alternatives considered: Creating new subsystems or compatibility wrappers was rejected because MoonMind is pre-release and internal contracts should fail fast rather than gain compatibility aliases.

## Unit Test Strategy

Decision: Use focused Python unit coverage through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py` and focused UI coverage through `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx`.
Rationale: Unit tests can prove validation, serialization, redaction, policy, and state behavior without requiring Docker credentials or external providers.
Alternatives considered: Provider verification tests were rejected for this planning phase because MM-318 requires deterministic local evidence first.

## Integration Test Strategy

Decision: Use compose-backed hermetic integration coverage through `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/temporal/test_oauth_session.py`.
Rationale: Stories touching API, Temporal, managed runtime, Docker, or browser transport need boundary evidence beyond isolated unit tests.
Alternatives considered: Skipping integration was rejected; only local execution may be blocked when `/var/run/docker.sock` is unavailable.

## Source Design Coverage

Decision: Preserve in-scope source coverage IDs `DESIGN-REQ-001, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-020` and keep all other MM-318 stories out of scope for this spec.
Rationale: Each generated spec must remain a single independently testable story while still preserving traceability to the broad OAuthTerminal design.
Alternatives considered: Combining stories was rejected because it would violate the one-story MoonSpec gate.
