# Contract: OAuth Runner Bootstrap PTY

## Story Boundary

Source story: MM-361 Jira preset brief for replacing placeholder Codex OAuth auth runner behavior with provider bootstrap PTY lifecycle.

## Inputs

- Jira traceability: `MM-361` and preset brief `MM-361: [OAuthTerminal] Replace placeholder auth runner with provider bootstrap PTY lifecycle`.
- Source design coverage: `DESIGN-REQ-011`, `DESIGN-REQ-012`, `DESIGN-REQ-014`, `DESIGN-REQ-020`.
- OAuth session activity request shape:

```json
{
  "session_id": "oas_example",
  "runtime_id": "codex_cli",
  "volume_ref": "codex_auth_volume",
  "volume_mount_path": "/home/app/.codex",
  "session_ttl": 1800
}
```

- Provider registry fields required by runner startup: `runtime_id`, `session_transport`, `default_volume_name`, `default_mount_path`, and non-empty `bootstrap_command`.

## Required Behavior

- `oauth_session.start_auth_runner` resolves the provider bootstrap command from the provider registry using `runtime_id`.
- Runner startup mounts `volume_ref` at `volume_mount_path` for the provider enrollment command.
- Runner startup executes the provider bootstrap command as the session-owned terminal process instead of placeholder sleep behavior.
- Runner terminal access is represented only through the OAuth terminal bridge metadata.
- Generic Docker exec and ordinary managed task terminal attachment are rejected or omitted for OAuth runner sessions.
- Success, failure, expiry, cancellation, and API-finalize paths stop the runner through idempotent cleanup.
- Startup, command, and cleanup failures return bounded redacted reasons.

## Outputs

Successful auth runner startup returns secret-free metadata:

```json
{
  "container_name": "moonmind_auth_oas_example",
  "terminal_session_id": "term_oas_example",
  "terminal_bridge_id": "br_oas_example",
  "session_transport": "moonmind_pty_ws",
  "expires_at": "2026-04-16T18:30:00+00:00"
}
```

Runner cleanup returns a secret-free idempotent outcome:

```json
{
  "session_id": "oas_example",
  "container_name": "moonmind_auth_oas_example",
  "stopped": true
}
```

## Failure Behavior

- Missing `session_id`, `runtime_id`, `volume_ref`, `volume_mount_path`, or provider bootstrap command fails before externally visible runner side effects.
- Missing Docker, mount failure, runner startup timeout, runner command failure, and provider command rejection produce actionable redacted reasons.
- Failure payloads must not contain raw credential contents, token values, private keys, environment dumps, or raw auth-volume listings.
- Integration blockers such as an unavailable Docker socket are recorded as blockers, not treated as passing evidence.

## Compatibility And Boundary Requirements

- The workflow-bound `oauth_session.start_auth_runner` request shape remains compatible with existing worker invocation; provider command resolution happens inside the activity/runtime boundary.
- OAuth terminal runner evidence remains separate from managed Codex task execution evidence.
- No compatibility aliases or hidden fallback commands are introduced for unsupported runtime values.
