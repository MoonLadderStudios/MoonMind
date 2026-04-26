# Contract: `deployment.update_compose_stack` Tool Definition

## Registry Payload

The canonical builder returns a payload parseable by `parse_tool_definition`:

```yaml
name: deployment.update_compose_stack
version: 1.0.0
type: skill
executor:
  activity_type: mm.tool.execute
  selector:
    mode: by_capability
requirements:
  capabilities:
    - deployment_control
    - docker_admin
security:
  allowed_roles:
    - admin
policies:
  timeouts:
    start_to_close_seconds: 900
    schedule_to_close_seconds: 1800
  retries:
    max_attempts: 1
    non_retryable_error_codes:
      - INVALID_INPUT
      - PERMISSION_DENIED
      - POLICY_VIOLATION
      - DEPLOYMENT_LOCKED
```

## Valid Plan Node

```json
{
  "id": "update-moonmind-deployment",
  "tool": {
    "type": "skill",
    "name": "deployment.update_compose_stack",
    "version": "1.0.0"
  },
  "inputs": {
    "stack": "moonmind",
    "image": {
      "repository": "ghcr.io/moonladderstudios/moonmind",
      "reference": "20260425.1234"
    },
    "mode": "changed_services",
    "removeOrphans": true,
    "wait": true,
    "runSmokeCheck": true,
    "pauseWork": false,
    "pruneOldImages": false,
    "reason": "Update to the latest tested MoonMind build"
  }
}
```

## Rejected Plan Inputs

The input schema rejects these fields before execution:

- `command`
- `shell`
- `composeFile`
- `composeFiles`
- `hostPath`
- `composeProjectDir`
- `updaterRunnerImage`
- unrecognized flags
