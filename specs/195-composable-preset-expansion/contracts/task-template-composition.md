# Contract: Task Template Composition

## Preset Version Step Union

Concrete step entry:

```json
{
  "kind": "step",
  "title": "Run tests",
  "instructions": "Run the test suite.",
  "skill": {"id": "auto", "args": {}},
  "annotations": {}
}
```

Existing entries without `kind` are treated as `kind: step`.

Include entry:

```json
{
  "kind": "include",
  "slug": "child-preset",
  "version": "1.0.0",
  "alias": "child-checks",
  "scope": "global",
  "inputMapping": {
    "feature_request": "{{ inputs.feature_request }}"
  }
}
```

Rules:
- `version` is required and pinned.
- `alias` is required.
- `inputMapping` is optional and supplies child preset inputs.
- Child step overrides are not accepted.
- Global parent presets cannot include personal child presets.

## Expand Response

The expand response remains compatible with existing consumers:

```json
{
  "steps": [
    {
      "id": "tpl:parent:1.0.0:01:abcd1234",
      "title": "Child step",
      "instructions": "Do work",
      "skill": {"id": "auto", "args": {}},
      "presetProvenance": {
        "root": {"slug": "parent", "version": "1.0.0"},
        "source": {
          "slug": "child",
          "version": "1.0.0",
          "scope": "global",
          "stepIndex": 1
        },
        "alias": "child-checks",
        "path": ["parent@1.0.0", "child-checks:child@1.0.0"]
      }
    }
  ],
  "composition": {
    "slug": "parent",
    "version": "1.0.0",
    "scope": "global",
    "path": ["parent@1.0.0"],
    "stepIds": ["tpl:parent:1.0.0:01:abcd1234"],
    "includes": []
  },
  "appliedTemplate": {
    "slug": "parent",
    "version": "1.0.0",
    "inputs": {},
    "stepIds": ["tpl:parent:1.0.0:01:abcd1234"],
    "appliedAt": "..."
  },
  "capabilities": ["codex"],
  "warnings": []
}
```

## Failure Contract

Expansion fails before returning executable steps when:
- Include path contains a cycle.
- Flattened step count exceeds root `max_step_count`.
- Include target is missing, unreadable, inactive, or input-incompatible.
- A global preset includes a personal preset.

Failure messages include the include path, for example:

```text
Preset include cycle detected at parent@1.0.0 -> child:child@1.0.0 -> parent:parent@1.0.0.
```

## Executor Boundary

Preset includes are resolved before execution-facing `PlanDefinition` storage. The executor receives concrete steps and provenance metadata only; it does not evaluate include graphs.
