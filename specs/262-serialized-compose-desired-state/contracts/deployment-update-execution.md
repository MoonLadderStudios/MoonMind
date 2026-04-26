# Deployment Update Execution Contract

Traceability: `MM-520`, FR-001 through FR-012, DESIGN-REQ-001 through DESIGN-REQ-006.

## Tool Handler

The `deployment.update_compose_stack` v1.0.0 handler accepts the existing MM-519 typed tool input:

```json
{
  "stack": "moonmind",
  "image": {
    "repository": "ghcr.io/moonladderstudios/moonmind",
    "reference": "20260425.1234",
    "resolvedDigest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "mode": "changed_services",
  "removeOrphans": true,
  "wait": true,
  "runSmokeCheck": true,
  "pauseWork": false,
  "pruneOldImages": false,
  "reason": "Update to the latest tested MoonMind build"
}
```

## Execution Guarantees

- The handler acquires a per-stack lock before side effects.
- If the lock is already held, the handler raises non-retryable `DEPLOYMENT_LOCKED`.
- Before-state capture occurs before desired-state persistence.
- Desired-state persistence occurs before pull and up commands.
- Pull is built as typed command arguments equivalent to `docker compose pull --policy always --ignore-buildable`.
- Up is built as typed command arguments equivalent to `docker compose up -d`, with only the policy-controlled `--force-recreate`, `--remove-orphans`, and `--wait` flags added when applicable.
- Runner mode is a deployment-controlled value: `privileged_worker` or `ephemeral_updater_container`.
- Runner image, Compose paths, host paths, shell snippets, and arbitrary flags are not accepted by this contract.

## Result Shape

The handler returns `ToolResult.outputs` matching the existing tool output schema:

```json
{
  "status": "SUCCEEDED",
  "stack": "moonmind",
  "requestedImage": "ghcr.io/moonladderstudios/moonmind:20260425.1234",
  "resolvedDigest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "updatedServices": ["api"],
  "runningServices": [{"name": "api", "state": "running", "health": "healthy"}],
  "beforeStateArtifactRef": "artifact:before",
  "afterStateArtifactRef": "artifact:after",
  "commandLogArtifactRef": "artifact:commands",
  "verificationArtifactRef": "artifact:verification"
}
```

Verification failure returns `status = FAILED`, never `SUCCEEDED`.
