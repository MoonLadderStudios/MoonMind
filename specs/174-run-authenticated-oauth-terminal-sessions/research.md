# Research

## Decisions

- Keep the existing `managed_agent_oauth_sessions` table as the session transport state store.
  Rationale: It already contains terminal refs, connection timestamps, runner container name, expiry, status, owner, and failure reason fields.

- Use a MoonMind-owned transport value of `moonmind_pty_ws` when a terminal bridge is available.
  Rationale: The design explicitly distinguishes this from old external terminal URL models.

- Resolve provider bootstrap commands from `moonmind.workflows.temporal.runtime.providers.registry`.
  Rationale: Provider-specific login commands belong behind the runtime provider registry and can evolve without changing the terminal bridge contract.

- Keep credential verification at existing volume verifier boundaries.
  Rationale: Verification must inspect credential presence/fingerprint without copying raw credential contents into API responses, logs, artifacts, or workflow payloads.

## Alternatives Considered

- Add a new `oauth_terminal_sessions` table.
  Rejected for this story because the existing OAuth session row has the needed state and avoids unnecessary schema churn.

- Expose a generic command parameter over the WebSocket.
  Rejected because the terminal bridge must not become generic Docker exec.
