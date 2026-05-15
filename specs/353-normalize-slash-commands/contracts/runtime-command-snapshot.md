# Contract: Runtime Command Snapshot Normalization

Source traceability: MM-684 canonical Jira preset brief.

## Input Surface

The canonical task payload may include authored instructions at:

```json
{
  "task": {
    "instructions": "/review\nCheck the branch.",
    "runtime": {"mode": "codex"},
    "steps": [
      {
        "id": "step-1",
        "instructions": "/simplify\nReduce duplication."
      }
    ]
  }
}
```

Clients may also send preview metadata in `runtimeCommand`, but backend normalization is authoritative. Conflicting or malformed supplied metadata is invalid.

## Snapshot Output

`build_authoritative_task_input_snapshot()` returns preserved authored instructions plus derived command metadata:

```json
{
  "objective": {
    "instructions": "/review\nCheck the branch.",
    "runtimeCommand": {
      "kind": "slash_command",
      "source": "leading_slash",
      "sourcePath": "objective.instructions",
      "command": "review",
      "rawCommand": "/review",
      "args": "",
      "instructionBody": "Check the branch.",
      "targetRuntime": "codex",
      "detectionStatus": "detected",
      "hintStatus": "hinted",
      "recognitionMode": "hinted_runtime_passthrough",
      "requiresRuntimeRecognition": true,
      "runtimeCapabilityVersion": "2026-05-13",
      "hintCatalogVersion": "2026-05-13",
      "detectionPhase": "submit"
    }
  },
  "steps": [
    {
      "id": "step-1",
      "instructions": "/simplify\nReduce duplication.",
      "runtimeCommand": {
        "kind": "slash_command",
        "source": "leading_slash",
        "sourcePath": "steps[0].instructions",
        "targetStepId": "step-1",
        "command": "simplify"
      }
    }
  ]
}
```

## Parser Contract

- Detect only when the normalized instruction string's first character is `/`.
- Treat `\/` as an escaped literal.
- Parse structured commands with:

```regex
^\/([A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z0-9_-]+)?)(?:\s+(.*))?$
```

- Preserve opaque slash-leading command lines for pass-through runtimes when they are not ordinary path-like text.
- Treat ordinary path-like first lines such as `/src/app.ts is broken` as malformed literal input.
- Do not use known-command hints as an allowlist.

## Validation Contract

- Backend-generated metadata must match authored instructions and source path.
- Supplied metadata with a command, status, source path, or target step that conflicts with backend parsing is rejected.
- Supplied metadata with unsupported shape is rejected.
- Absence of supplied metadata is valid; backend computes metadata.
