# Contract: Step Type Payloads

## Draft-Oriented Payload

Draft and edit reconstruction surfaces must preserve explicit Step Type intent:

```ts
type DraftStepType = "tool" | "skill" | "preset";

type DraftStep = {
  id: string;
  title: string;
  instructions: string;
  stepType: DraftStepType;
  tool?: {
    id?: string;
    name?: string;
    inputs?: Record<string, unknown>;
  };
  skill?: {
    id?: string;
    name?: string;
    inputs?: Record<string, unknown>;
    args?: Record<string, unknown>;
  };
  preset?: {
    id?: string;
    slug?: string;
    version?: string;
    inputs?: Record<string, unknown>;
  };
};
```

Legacy readers may infer `stepType` from older `tool` or `skill` fields when no explicit type is present, but new reconstructed draft output should expose the inferred `stepType`.

## Executable Submission Payload

Executable submission accepts only:

```ts
type ExecutableStep = ToolStep | SkillStep;
```

Unresolved `PresetStep` payloads, Temporal Activity labels, and mixed Tool/Skill payloads must fail validation before runtime materialization.

## Compatibility Boundary

Compatibility belongs in reconstruction/read paths only. It must not make Preset an executable runtime node or make Activity a user-facing Step Type.
