# Contract: Claude Policy Envelope

Source issue: `MM-343`

## Public Schema Surface

The runtime schema surface exposes these importable contracts from `moonmind.schemas`:

- `ClaudePolicyEnvelope`
- `ClaudePolicySource`
- `ClaudePolicyHandshake`
- `ClaudePolicyEvent`
- `resolve_claude_policy_envelope`

## Policy Resolution Input

```python
resolve_claude_policy_envelope(
    *,
    session_id: str,
    policy_envelope_id: str,
    provider_mode: ClaudeProviderMode,
    sources: tuple[ClaudePolicySource, ...],
    version: int = 1,
    interactive: bool = False,
    fail_closed_on_refresh_failure: bool = False,
) -> tuple[ClaudePolicyEnvelope | None, ClaudePolicyHandshake, tuple[ClaudePolicyEvent, ...]]
```

Rules:
- Server-managed non-empty supported settings win over endpoint-managed settings.
- Endpoint-managed settings apply only when server-managed settings are empty or unsupported.
- Local project, shared project, user, and CLI settings are retained only for observability evidence.
- `fail_closed_on_refresh_failure=True` plus a fail-closed source state returns no permissive envelope and a `fail_closed` handshake.
- Risky managed hooks or managed environment variables require a security-dialog handshake in interactive sessions.
- Non-interactive dialog-required scenarios produce a blocked handshake.
- BootstrapPreferences are emitted only as bootstrap templates.

## Wire Shape

All models serialize using camelCase aliases.

Required envelope fields:

```json
{
  "policyEnvelopeId": "policy-1",
  "sessionId": "claude-session-1",
  "providerMode": "anthropic_api",
  "managedSourceKind": "server_managed",
  "policyFetchState": "fetched",
  "policyTrustLevel": "server_managed_best_effort",
  "permissions": {
    "mode": "default",
    "allow": [],
    "ask": [],
    "deny": [],
    "protectedPaths": [],
    "autoModeEnabled": false,
    "bypassDisabled": false,
    "autoDisabled": false
  },
  "sandbox": {
    "enabled": false,
    "filesystemScope": {},
    "networkScope": {}
  },
  "hooks": {
    "allowManagedOnly": false,
    "registryHash": null
  },
  "mcp": {
    "allowedServers": [],
    "deniedServers": [],
    "allowManagedOnly": false
  },
  "memory": {
    "includeAutoMemory": true,
    "managedClaudeMdEnabled": false,
    "excludes": []
  },
  "bootstrapTemplates": [],
  "securityDialogRequired": false,
  "version": 1,
  "adminVisibility": {
    "fetchState": "fetched",
    "managedSourceKind": "server_managed",
    "policyTrustLevel": "server_managed_best_effort"
  },
  "userVisibility": {
    "status": "managed"
  }
}
```

## Events

Event types:
- `policy.fetch.started`
- `policy.fetch.succeeded`
- `policy.fetch.failed`
- `policy.dialog.required`
- `policy.dialog.accepted`
- `policy.dialog.rejected`
- `policy.compiled`
- `policy.version.changed`

Events must reference `sessionId` and may reference `policyEnvelopeId` when an envelope exists.

## Compatibility

This is a pre-release internal contract. Unsupported enum values fail validation. The implementation must not add compatibility aliases or translate Codex managed-default semantics into Claude native managed settings.
