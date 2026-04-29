# Contract: Step Type Runtime and Proposal Promotion

## Executable Task Step Contract

Executable task payloads accepted for runtime materialization contain flat concrete steps:

```ts
type ExecutableStep = ToolStep | SkillStep;
```

`PresetStep` is not executable by default. Preset-derived work must be represented as concrete Tool or Skill steps before submission. Preset provenance may be attached through `source` metadata but must not be required to execute the step.

## Runtime Materialization Contract

| Input Step Type | Runtime Materialization |
| --- | --- |
| `tool` | Typed tool plan node with selected tool identifier and inputs. |
| `skill` | Agent runtime plan node with selected skill identifier and inputs. |
| `preset` | Invalid at executable boundary; no runtime node by default. |
| `activity` / `Activity` | Invalid user-facing Step Type. |

## Proposal Promotion Contract

Stored proposals are promotable when `taskCreateRequest.payload` validates as a canonical task payload.

Promotion behavior:
- Loads the stored reviewed task payload.
- Validates the flat payload before execution.
- Preserves reviewed steps, instructions, authored preset metadata, and step source metadata.
- May apply explicit bounded runtime override controls.
- Does not call live preset lookup or silently re-expand catalog entries for correctness.
- Rejects unresolved Preset steps and invalid Activity labels.

## Preview Contract

Proposal preview may summarize preset provenance from stored metadata:
- `manual`: no authored preset or preset-derived source metadata is present.
- `preserved-binding`: authored preset bindings are present.
- `flattened-only`: step source metadata indicates preset-derived work without authored bindings.

Preview metadata is audit and review data; it is not execution input.
