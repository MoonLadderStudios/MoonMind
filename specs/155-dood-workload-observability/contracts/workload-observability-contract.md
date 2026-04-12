# Contract: DooD Workload Observability

## 1. Workload Result Output Contract

Docker-backed workload execution returns a normal executable tool result. The result must stay bounded and artifact-reference-first.

### Required result fields

| Field | Required | Description |
| --- | --- | --- |
| `requestId` | Yes | Stable workload request/container identity for the attempt. |
| `profileId` | Yes | Selected runner profile id. |
| `workloadStatus` | Yes | Final workload status such as succeeded, failed, timed out, or canceled. |
| `exitCode` | No | Process exit code when available. |
| `stdoutRef` | Yes after finalization | Durable runtime stdout artifact reference. |
| `stderrRef` | Yes after finalization | Durable runtime stderr artifact reference. |
| `diagnosticsRef` | Yes after finalization | Durable runtime diagnostics artifact reference. |
| `outputRefs` | Yes | Mapping of runtime and declared output artifact classes to artifact refs. |
| `workloadMetadata` | Yes | Bounded step/profile/session association metadata. |

### Required `outputRefs` classes

| Class | Meaning |
| --- | --- |
| `runtime.stdout` | Captured workload stdout. |
| `runtime.stderr` | Captured workload stderr. |
| `runtime.diagnostics` | Structured workload diagnostics. |
| `output.logs` | General log output reference when a consumer expects normal tool log semantics. |

Declared output classes such as `output.primary`, `output.summary`, `test.report`, or domain-specific classes may be present when the workload declares and produces them.

### Forbidden default classes

Workload output publication must not create these classes by default:

- `session.summary`
- `session.step_checkpoint`
- `session.control_event`
- `session.reset_boundary`

## 2. Diagnostics Artifact Contract

The diagnostics artifact is JSON-compatible and must contain enough information to diagnose the workload without inspecting the container.

| Field | Required | Description |
| --- | --- | --- |
| `status` | Yes | Final workload status. |
| `taskRunId` | Yes | Owning task run. |
| `stepId` | Yes | Producing step. |
| `attempt` | Yes | Attempt number. |
| `toolName` | Yes | Executable tool name. |
| `profileId` | Yes | Selected runner profile. |
| `imageRef` | Yes | Image reference selected by the profile. |
| `containerName` | Yes | Deterministic workload container name or equivalent request id. |
| `exitCode` | No | Exit code when available. |
| `durationSeconds` | No | Runtime duration when available. |
| `timeoutReason` | No | Timeout reason when applicable. |
| `cancelReason` | No | Cancel reason when available. |
| `labels` | Yes | MoonMind ownership labels. |
| `declaredOutputs` | Yes | Declared output class-to-path map, possibly empty. |
| `declaredOutputRefs` | Yes | Produced declared output refs, possibly empty. |
| `missingDeclaredOutputs` | Yes | Missing declared output diagnostics, possibly empty. |
| `sessionContext` | No | Association metadata only. |

## 3. Declared Output Contract

Declared outputs are caller-provided expected outputs under the workload artifacts directory.

Rules:

- Paths must be relative to the workload artifacts directory.
- Paths must not escape the workload artifacts directory after normalization.
- Classes must not use session continuity classes.
- Existing declared outputs are added to `outputRefs`.
- Missing declared outputs are listed in diagnostics and do not suppress runtime stdout, stderr, or diagnostics publication.

Example:

```json
{
  "declaredOutputs": {
    "test.report": "reports/results.xml",
    "output.summary": "summary.json"
  }
}
```

## 4. Step Projection Contract

Execution detail and task detail projections must expose workload evidence through the producing step.

Required projection fields:

| Field | Description |
| --- | --- |
| `refs.taskRunId` | Owning task run when the workload result provides it. |
| `artifacts.runtimeStdout` | Runtime stdout artifact ref. |
| `artifacts.runtimeStderr` | Runtime stderr artifact ref. |
| `artifacts.runtimeDiagnostics` | Runtime diagnostics artifact ref. |
| `artifacts.outputPrimary` | Preferred declared output ref when available. |
| `artifacts.outputSummary` | Declared summary output ref when available. |
| `workload.taskRunId` | Owning task run. |
| `workload.stepId` | Producing step. |
| `workload.attempt` | Attempt number. |
| `workload.toolName` | Tool that launched the workload. |
| `workload.profileId` | Runner profile. |
| `workload.imageRef` | Profile-selected image reference. |
| `workload.status` | Workload terminal status. |
| `workload.exitCode` | Workload process exit code when available. |
| `workload.durationSeconds` | Workload runtime duration when available. |
| `workload.timeoutReason` | Timeout reason when applicable. |
| `workload.cancelReason` | Cancel reason when available. |
| `workload.sessionContext` | Optional grouping context. |

Projection rules:

- Workload step metadata must be available without starting or inspecting the workload container.
- Session association metadata may group the workload with a session turn but must not create session identity.
- API/UI consumers should display workload artifacts as workload/step outputs, not as session continuity artifacts.

## 5. Failure Contract

If artifact publication fails after workload execution:

- The workload result must not silently claim complete observability.
- The diagnostic outcome must identify which publication step failed when possible.
- Available partial refs should remain visible when they were successfully produced.
- The producing step should surface an operator-visible failure or degraded-observability status.
