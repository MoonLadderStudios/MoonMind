# Contract: Flattened Executable Steps With Provenance

## Executable Submission Contract

Default executable task submissions contain concrete Tool and Skill steps only.

Accepted executable Step Types:
- `tool`
- `skill`

Rejected by default at executable boundaries:
- `preset`
- `activity`
- `Activity`
- arbitrary script or shell-shaped step overrides

Preset-derived work must be applied before submission. Applying a preset replaces the temporary Preset placeholder with concrete executable steps.

## Preset Provenance Contract

Preset-derived executable steps carry source metadata when available:

```json
{
  "source": {
    "kind": "preset-derived",
    "presetId": "jira.implementation_flow",
    "presetVersion": "2026-04-28",
    "includePath": ["root", "implementation"],
    "originalStepId": "implement"
  }
}
```

Rules:
- Preset application, review, and proposal surfaces preserve `source.kind`, `source.presetId`, `source.presetVersion`, and `source.originalStepId` for preset-derived steps when the expansion source provides those values.
- `source.includePath` is preserved when the expansion source provides an include path.
- Source metadata is audit and reconstruction data, not hidden runtime work.
- Missing, stale, partial, or unavailable source metadata must not force runtime catalog lookup when the executable Tool/Skill payload is otherwise valid.

## Runtime Materialization Contract

| Submitted Step | Runtime behavior |
| --- | --- |
| Tool step | Materializes as a typed tool execution node. |
| Skill step | Materializes as an agent-facing skill/runtime execution node. |
| Preset step | Rejected before runtime execution by default. |

Runtime materialization must use the flat executable payload, not live preset catalog state.

## Proposal Promotion Contract

Stored promotable proposals contain a reviewed flat executable payload.

Promotion must:
- validate the stored payload before execution,
- preserve reviewed steps, instructions, authored preset bindings, and step source metadata,
- reject unresolved Preset steps,
- avoid live preset re-expansion unless the operator explicitly requests refresh through preview and validation,
- keep any runtime override bounded to runtime selection fields without rewriting reviewed steps or provenance.

## Explicit Refresh Contract

Refreshing a draft or proposal from a preset catalog entry is an explicit operator action.

Refresh must:
- show a preview of replacement executable steps,
- validate generated steps before mutation,
- preserve the current reviewed payload if preview or validation fails,
- replace the stored flat payload only after explicit confirmation.
