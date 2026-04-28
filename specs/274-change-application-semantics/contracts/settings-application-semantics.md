# Contract: Settings Application Semantics

## Descriptor Contract

Settings catalog descriptors returned from `GET /api/v1/settings/catalog` must include:

```json
{
  "key": "workflow.default_publish_mode",
  "apply_mode": "next_task",
  "requires_reload": false,
  "requires_worker_restart": false,
  "requires_process_restart": false,
  "applies_to": ["task_creation", "publishing"]
}
```

Rules:
- `apply_mode` is required for every editable descriptor.
- Restart/reload flags must align with `apply_mode`.
- `applies_to` identifies affected subsystems, not implementation modules.

## Change Event Contract

Settings audit/change responses exposed through `GET /api/v1/settings/audit` must include structured application metadata for committed changes:

```json
{
  "event_type": "settings.override.updated",
  "key": "workflow.default_publish_mode",
  "scope": "workspace",
  "source": "workspace_override",
  "apply_mode": "next_task",
  "affected_systems": ["task_creation", "publishing"],
  "actor_user_id": "00000000-0000-0000-0000-000000000000",
  "created_at": "2026-04-28T00:00:00Z"
}
```

Rules:
- Secret-bearing values remain redacted according to descriptor policy.
- Events must be sufficient for clients to explain when the change takes effect.

## Diagnostics Contract

Settings diagnostics returned from `GET /api/v1/settings/diagnostics` must expose activation and restored-reference state without plaintext secrets:

```json
{
  "key": "integrations.github.token_ref",
  "scope": "workspace",
  "apply_mode": "next_launch",
  "activation_state": "pending_next_boundary",
  "affected_systems": ["github", "integrations"],
  "completion_guidance": "New tasks will use this value on their next launch.",
  "diagnostics": [
    {
      "code": "unresolved_secret_ref",
      "message": "The configured SecretRef cannot be resolved.",
      "severity": "error",
      "details": {"reference_type": "secret_ref", "launch_blocker": true}
    }
  ]
}
```

Rules:
- Diagnostics must identify broken SecretRef, OAuth volume, and provider profile references where those references are represented by Settings.
- Diagnostics must never include raw managed secret plaintext, OAuth state, decrypted files, or generated credential config.

## UI Contract

Mission Control Settings must show, for restart/reload-relevant settings:
- apply mode,
- affected subsystem or worker/process,
- current effective value,
- pending value when applicable,
- active/pending state,
- completion guidance,
- restored-reference diagnostics.
