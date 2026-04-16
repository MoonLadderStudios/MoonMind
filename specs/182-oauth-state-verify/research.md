# Research: OAuth Session State and Verification Boundaries

## Implementation Boundary

Decision: Implement transport-neutral OAuth status and secret-free verification boundaries in existing MoonMind modules: moonmind/workflows/temporal/workflows/oauth_session.py, moonmind/workflows/temporal/activities/oauth_session_activities.py, moonmind/workflows/temporal/runtime/providers/registry.py, moonmind/workflows/temporal/runtime/providers/volume_verifiers.py.
Rationale: The source design requires thin orchestration boundaries and explicit ownership rather than new parallel runtime systems.
Alternatives considered: Creating new subsystems or compatibility wrappers was rejected because MoonMind is pre-release and internal contracts should fail fast rather than gain compatibility aliases.

## Unit Test Strategy

Decision: Use focused pytest coverage through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_session_activities.py tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_volume_verifiers.py`.
Rationale: Unit tests can prove validation, serialization, redaction, policy, and state behavior without requiring Docker credentials or external providers.
Alternatives considered: Provider verification tests were rejected for this planning phase because MM-318 requires deterministic local evidence first.

## Integration Test Strategy

Decision: Use compose-backed hermetic integration coverage through `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/temporal/test_oauth_session.py`.
Rationale: Stories touching API, Temporal, managed runtime, Docker, or browser transport need boundary evidence beyond isolated unit tests.
Alternatives considered: Skipping integration was rejected; only local execution may be blocked when `/var/run/docker.sock` is unavailable.

## Source Design Coverage

Decision: Preserve in-scope source coverage IDs `DESIGN-REQ-010, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-020` and keep all other MM-318 stories out of scope for this spec.
Rationale: Each generated spec must remain a single independently testable story while still preserving traceability to the broad OAuthTerminal design.
Alternatives considered: Combining stories was rejected because it would violate the one-story MoonSpec gate.
