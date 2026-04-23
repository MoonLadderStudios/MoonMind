# Research: Claude OAuth Session Backend

## FR-001 / SC-001 OAuth Session Creation

Decision: Implemented unverified; add a route-level test that `claude_anthropic` session creation stores `session_transport = moonmind_pty_ws` and remains anchored to the profile row.
Evidence: `api_service/api/routers/oauth_sessions.py` creates `ManagedAgentOAuthSession` from the request profile ID and provider defaults.
Rationale: The existing route is generic and should work once provider defaults are corrected, but there is no focused Claude assertion.
Alternatives considered: Adding a Claude-specific endpoint was rejected because the source brief requires reusing the OAuth Session API.
Test implications: Route-level async pytest.

## FR-002 / DESIGN-REQ-010 Provider Registry Defaults

Decision: Partial; update `claude_code` registry data to use `moonmind_pty_ws` and `["claude", "login"]`.
Evidence: `moonmind/workflows/temporal/runtime/providers/registry.py` currently registers `claude_code` with `session_transport = "none"` and `bootstrap_command = ["true"]`.
Rationale: The registry is the existing runtime boundary for OAuth defaults and bootstrap command resolution.
Alternatives considered: Hardcoding Claude behavior in the activity or router was rejected because provider-specific values belong in registry data.
Test implications: Unit provider registry tests.

## FR-003 / DESIGN-REQ-006 Auth Runner Startup

Decision: Partial; the activity already passes provider bootstrap commands, so fixing registry data and adding a Claude-specific activity test should complete this row.
Evidence: `oauth_session_start_auth_runner` calls `get_provider_bootstrap_command(runtime_id)` and passes `bootstrap_command` to `start_terminal_bridge_container`.
Rationale: The missing piece is the provider command, not a new activity contract.
Alternatives considered: Passing command from API request was rejected because the source requires provider registry ownership.
Test implications: Unit activity test.

## FR-004 / DESIGN-REQ-011 Claude Runner Home Environment

Decision: Missing; add runtime-specific auth-runner environment variables for Claude.
Evidence: `terminal_bridge.py` currently sets `HOME`, `CODEX_HOME`, `CODEX_CONFIG_HOME`, and `CODEX_CONFIG_PATH` for every runtime.
Rationale: Claude login writes to Claude home and the source design requires `CLAUDE_HOME` and `CLAUDE_VOLUME_PATH`.
Alternatives considered: Setting both Codex and Claude variables for every runtime was rejected because it leaks provider-specific behavior into unrelated runtimes.
Test implications: Unit terminal bridge runner argument test.

## FR-005 / DESIGN-REQ-012 API-Key Conflict Clearing

Decision: Missing; add explicit empty environment assignments for Claude runner startup so ambient API-key values do not reach the OAuth enrollment process.
Evidence: No runner argument currently clears `ANTHROPIC_API_KEY` or `CLAUDE_API_KEY`.
Rationale: Docker `-e KEY=` creates an empty value in the child container, overriding ambient worker variables without exposing secrets.
Alternatives considered: Relying only on profile `clear_env_keys` was rejected because this story specifically covers the enrollment runner, not just normal launch materialization.
Test implications: Unit terminal bridge runner argument test.

## FR-006 / FR-007 Seeded Provider Profile

Decision: Partial; keep `claude_anthropic` as OAuth-volume/OAuth-home and add `CLAUDE_API_KEY` to its conflict-clearing list.
Evidence: `api_service/main.py` seeds `claude_anthropic` with OAuth volume fields and currently clears `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`.
Rationale: The source profile shape includes Claude and Anthropic conflict clearing, and launch stories rely on the seeded profile shape.
Alternatives considered: Migrating existing DB rows in this story was rejected because no new persistent schema is needed and startup reconciliation handles seed metadata.
Test implications: Startup seed unit test.

## FR-008 / FR-009 Scope And Secret Safety

Decision: Implemented unverified; preserve existing terminal/session boundaries and verify no raw key values are passed in runner args.
Evidence: OAuth terminal bridge records bounded metadata and redacts startup errors; sessions are separate from managed task launch.
Rationale: MM-478 changes provider defaults and runner env only; it does not require exposing terminal input or launch payloads.
Alternatives considered: Extending normal Claude task terminal behavior was rejected because the brief explicitly scopes OAuth terminal sessions to enrollment and repair.
Test implications: Existing terminal bridge tests plus new Claude runner test.
