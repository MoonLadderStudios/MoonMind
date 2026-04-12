# Data Model: Temporal Task Editing Entry Points

## Temporal Execution Editing Read Contract

Represents the detail payload used to decide whether an execution can be edited or rerun and to prepare later draft reconstruction.

### Fields

- `workflowId`: Stable Temporal workflow identifier.
- `workflowType`: Workflow type; Phase 0/1 support is limited to `MoonMind.Run`.
- `state` / `rawState`: Current lifecycle state used for operator messaging and backend capability computation.
- `temporalStatus`: Compact Temporal status for display and lifecycle distinction.
- `inputParameters`: Current structured input parameters available for draft reconstruction.
- `inputArtifactRef`: Artifact reference for task instructions or input content when externalized.
- `planArtifactRef`: Artifact reference for plan or structured execution input when present.
- `targetRuntime`: Runtime selected for the execution.
- `profileId`: Provider profile selected for the execution.
- `model` / `requestedModel` / `resolvedModel`: Model selection state available to prefill later form modes.
- `effort`: Runtime effort setting when present.
- `repository`: Repository identifier associated with the task.
- `startingBranch`: Starting branch or default branch state.
- `targetBranch`: Target/publish branch state.
- `publishMode`: Publish behavior selected for the task.
- `targetSkill` / `taskSkills`: Skill selection state.
- `actions`: State-aware capability set.

### Validation Rules

- `workflowId` must be non-empty.
- `workflowType` must be `MoonMind.Run` before Edit or Rerun entry points are exposed.
- `inputParameters` must be an object; missing values are acceptable for Phase 1 visibility but must be treated as incomplete by later draft reconstruction.
- Artifact references are immutable historical references; edit/rerun submit phases must create new artifact references for changed content.

## Action Capability Set

Represents backend-owned visibility decisions for execution actions.

### Fields

- `canUpdateInputs`: True only when the execution supports in-place input updates.
- `canRerun`: True only when the execution supports rerun.
- `disabledReasons`: Optional map explaining why capabilities are unavailable.

### Validation Rules

- `canUpdateInputs` and `canRerun` must default to false.
- Capabilities must be false when the workflow type is not `MoonMind.Run`.
- Capabilities must be false when the `temporalTaskEditing` feature flag is disabled.
- Lifecycle-ineligible states must not expose edit/rerun capabilities.

## Temporal Task Editing Feature Flag

Represents the rollout control surfaced to Mission Control.

### Fields

- `temporalTaskEditing`: Boolean dashboard runtime flag.

### Validation Rules

- When false, the UI must omit Edit and Rerun entry points.
- The flag must not replace backend capability validation; both the flag and capability must allow the action.

## Task Editing Route Target

Represents canonical navigation from task detail to the shared submit page.

### Fields

- `mode`: One of `create`, `edit`, or `rerun`.
- `workflowId`: Required for `edit` and `rerun`; absent for `create`.
- `href`: Canonical URL.

### State Derivation

| Mode | Required Input | Canonical URL |
| --- | --- | --- |
| `create` | None | `/tasks/new` |
| `edit` | `workflowId` | `/tasks/new?editExecutionId=<workflowId>` |
| `rerun` | `workflowId` | `/tasks/new?rerunExecutionId=<workflowId>` |

### Validation Rules

- Route helpers must URL-encode `workflowId`.
- Route helpers must never emit `editJobId`.
- Route helpers must never emit `/tasks/queue/new`.

## Task Editing Fixture

Represents local/CI-safe test data for the Phase 0/1 slice.

### Fixture Types

- Supported active `MoonMind.Run` with `canUpdateInputs = true`.
- Supported terminal `MoonMind.Run` with `canRerun = true`.
- Active execution without update capability.
- Terminal execution without rerun capability.
- Unsupported workflow type.
- Feature-disabled execution that would otherwise be eligible.

### Validation Rules

- Fixtures must not require external provider credentials.
- Fixtures must cover both visible and omitted action states.
- Fixtures must include enough read contract fields to detect regressions in later draft reconstruction.

## Update Request Contract Placeholder

Represents update names and payload shape reserved for later submit phases.

### Fields

- `updateName`: `UpdateInputs` or `RequestRerun`.
- `inputArtifactRef`: New edited input artifact reference when content is externalized.
- `parametersPatch`: Structured edited input values.

### Validation Rules

- `UpdateInputs` is for active supported executions only.
- `RequestRerun` is for terminal supported executions only.
- Historical artifact references must not be mutated in place.
