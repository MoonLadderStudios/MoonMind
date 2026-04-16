# Research: OAuth Terminal PTY Proxy

## Runtime Boundary

Decision: Implement real PTY forwarding inside the OAuth terminal attach route by delegating socket operations to a shared terminal bridge helper.
Rationale: The OAuth attach endpoint already enforces one-time tokens, session status, ownership by requested user, TTL, and safe metadata. Moving forwarding there keeps OAuth enrollment separate from generic task terminal surfaces.
Alternatives considered: Reusing the generic `/terminal/{session_id}` route was rejected because it accepts a different authentication model and is explicitly not the OAuth terminal contract.

## PTY Adapter Shape

Decision: Add a small adapter abstraction in `moonmind/workflows/temporal/runtime/terminal_bridge.py` that can connect to the auth-runner container, send input bytes, stream output bytes, resize the PTY, and close safely.
Rationale: The existing `TerminalBridgeConnection` validates frame semantics but does not touch a PTY. Keeping Docker/socket details behind a helper makes unit tests deterministic and keeps router code focused on authentication and metadata.
Alternatives considered: Embedding Docker SDK calls directly in the OAuth router was rejected because it would duplicate `api_service/api/websockets.py` behavior and make frame tests harder to isolate.

## Frame Semantics

Decision: Treat text JSON frames with `type=input`, `type=resize`, `type=heartbeat`, and `type=close` as OAuth terminal control frames; treat binary messages as terminal input bytes; reject `exec`, `docker_exec`, `task_terminal`, and unknown JSON frame types.
Rationale: This preserves the browser terminal protocol already used by Mission Control while making unsupported generic terminal behavior fail fast.
Alternatives considered: Passing all unknown frames through to the PTY was rejected because it could hide task-terminal or Docker exec semantics behind OAuth enrollment.

## Output Handling

Decision: Stream auth-runner output to the browser as terminal data while persisting only safe metadata such as event counts, dimensions, heartbeat count, and close reason.
Rationale: The browser must see provider login terminal output, but workflow history, logs, artifacts, and database metadata must not store raw credential-like scrollback.
Alternatives considered: Persisting output excerpts was rejected because terminal scrollback may contain sensitive provider login data.

## Unit Test Strategy

Decision: Use focused pytest coverage for the bridge adapter and OAuth router WebSocket helpers with faked PTY streams.
Rationale: Unit tests can prove forwarding, resize, heartbeat, close, token, and rejection behavior without requiring Docker.
Alternatives considered: Only testing against Docker was rejected because provider and Docker availability should not be required for unit verification.

## Integration Test Strategy

Decision: Use `./tools/test_integration.sh` for Docker-backed OAuth session boundary coverage when Docker is available.
Rationale: Real auth-runner PTY forwarding crosses the API/runtime/container boundary and benefits from hermetic integration evidence.
Alternatives considered: Provider verification tests were rejected because MM-362 needs deterministic local evidence, not live provider credentials.
