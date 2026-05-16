# Contract: Historical Slash Command Fidelity

## Scope

This contract defines the expected behavior for MM-687 across Create/Edit/Rerun, Task Detail, and audit/observability surfaces. It does not define a new persistent store.

## Edit Mode Draft Contract

When loading an existing execution for edit:

```json
{
  "taskInstructions": "/review\nCheck the branch.",
  "runtimeCommand": {
    "kind": "slash_command",
    "sourcePath": "objective.instructions",
    "command": "review",
    "rawCommand": "/review",
    "recognitionMode": "hinted_runtime_passthrough",
    "runtimeCapabilityVersion": "2026-05-13",
    "hintCatalogVersion": "2026-05-13"
  }
}
```

Requirements:

- `taskInstructions` comes from the historical snapshot.
- `runtimeCommand` comes from the historical snapshot when present.
- If `runtimeCommand` is absent, the UI may compute a preview warning but must not write preview metadata back to the historical instruction value.

## Exact Rerun Contract

When the form is unchanged and an exact rerun is requested:

```json
{
  "action": "RequestRerun",
  "parametersPatch": null,
  "recovery": {
    "kind": "exact_full_rerun",
    "sourceWorkflowId": "mm:source",
    "sourceRunId": "run-source"
  }
}
```

Requirements:

- Original authored instructions are preserved.
- Original runtime command metadata is preserved, including capability and hint catalog versions.
- Current catalog warnings must be presented separately from source-run evidence.

## Edit-for-Rerun Contract

When an operator edits before rerun:

```json
{
  "action": "RequestRerun",
  "parametersPatch": {
    "task": {
      "instructions": "/review\nUpdated focus.",
      "recovery": {
        "kind": "edited_full_retry",
        "sourceWorkflowId": "mm:source",
        "sourceRunId": "run-source"
      }
    }
  }
}
```

Requirements:

- The editable copy may show current preview warnings.
- Source-run authored instructions and runtime command metadata remain immutable.
- Any recomputed warnings are labeled as current-preview information, not original run evidence.

## Task Detail Contract

Task details for a slash-command task must expose:

```json
{
  "originalInstructions": "/review\nCheck the branch.",
  "runtimeCommand": {
    "command": "review",
    "runtime": "claude_code",
    "renderMode": "prompt_prefix",
    "status": "passed_through",
    "runtimeCapabilityVersion": "2026-05-13",
    "hintCatalogVersion": "2026-05-13"
  }
}
```

Requirements:

- Original instructions are visible alongside interpretation.
- Missing interpretation is shown as missing historical metadata, not inferred as fact.
- User-authored values are displayed safely.

## Audit Event Contract

Detected command event:

```json
{
  "event": "runtime_command.detected",
  "runtimeId": "claude_code",
  "command": "review",
  "sourcePath": "objective.instructions",
  "hintStatus": "hinted",
  "recognitionMode": "hinted_runtime_passthrough",
  "runtimeCapabilityVersion": "2026-05-13",
  "hintCatalogVersion": "2026-05-13"
}
```

Rendered command event:

```json
{
  "event": "runtime_command.rendered",
  "runtimeId": "claude_code",
  "command": "review",
  "renderMode": "prompt_prefix"
}
```

Opaque pass-through event:

```json
{
  "event": "runtime_command.passthrough",
  "runtimeId": "claude_code",
  "command": "future-command",
  "hintStatus": "opaque",
  "renderMode": "prompt_prefix"
}
```

Requirements:

- Payloads contain no raw credentials, cookies, private keys, bearer tokens, or plaintext resolved secret refs.
- Unknown valid slash commands use pass-through events instead of fake known-command statuses.
- Event payloads remain compact and suitable for existing observability APIs.
