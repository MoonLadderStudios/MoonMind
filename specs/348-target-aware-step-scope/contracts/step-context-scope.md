# Contract: Step Context Scope and AgentRun Child Input

## Scope

This contract covers the parent workflow boundary that selects target-aware prepared context for one runtime step and passes that bounded context into `MoonMind.AgentRun` child workflows or external runtime adapters.

## Parent Selection Contract

Input:

```json
{
  "task": {
    "inputAttachments": [{"artifactId": "objective-image"}],
    "steps": [
      {"id": "collect-evidence", "inputAttachments": [{"artifactId": "collect-notes"}]},
      {"id": "write-report", "inputAttachments": [{"artifactId": "report-notes"}]}
    ]
  },
  "logicalStepId": "collect-evidence"
}
```

Required output metadata:

```json
{
  "manifestRef": "prepared-context-manifest://task-inputs",
  "logicalStepId": "collect-evidence",
  "objectiveContextRefs": ["prepared-context://objective/objective-image"],
  "stepContextRefs": ["prepared-context://steps/collect-evidence/collect-notes"],
  "rawInputRefs": ["artifact://objective-image", "artifact://collect-notes"],
  "inputRefs": [
    "prepared-context://objective/objective-image",
    "prepared-context://steps/collect-evidence/collect-notes",
    "artifact://objective-image",
    "artifact://collect-notes"
  ],
  "targetCounts": {"objective": 1, "step": 1}
}
```

Forbidden:
- `prepared-context://steps/write-report/report-notes`
- `artifact://report-notes`
- inline binary bytes, data URLs, or generated markdown bodies
- any target binding computed from filename, array index, path position, or instruction text

## AgentRun Child Input Contract

For a child workflow representing `collect-evidence`, the parent request MUST include:

- `AgentExecutionRequest.parameters.metadata.moonmind.preparedContext.logicalStepId = "collect-evidence"`
- objective context refs relevant to the task
- step context refs only for `collect-evidence`
- no prepared refs for sibling steps

For managed runtimes, prepared refs MAY stay in `parameters.metadata.moonmind.preparedContext.inputRefs` rather than `AgentExecutionRequest.inputRefs`, but the same exclusion rule applies.

## Diagnostic Contract

Child workflow logs, result metadata, or diagnostics MAY report:

- parent workflow/run identifiers
- represented `logicalStepId`
- bounded prepared context refs and target counts
- explicit rejection/failure reason for invalid prepared context

Child workflow logs, result metadata, or diagnostics MUST NOT:

- redefine which target owns an attachment
- broaden step context to sibling-step refs
- override parent-provided `preparedContext.logicalStepId`
- embed unrelated attachment contents

## Compatibility

Prepared-context metadata is a workflow boundary payload. Any shape change must either preserve worker-bound invocation compatibility for in-flight runs or use an explicit versioned cutover plan with boundary tests.
