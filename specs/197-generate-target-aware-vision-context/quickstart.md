# Quickstart: Generate Target-Aware Vision Context Artifacts

## Focused Unit Validation

```bash
./tools/test_unit.sh tests/unit/moonmind/vision/test_service.py
```

Expected evidence:

- Objective targets render `.moonmind/vision/task/image_context.md`.
- Step targets render `.moonmind/vision/steps/<stepRef>/image_context.md`.
- Disabled and provider-unavailable states are deterministic and explicit.
- Markdown and index entries preserve source attachment refs and local paths.

## Focused Integration Validation

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/vision/test_context_artifacts.py -q
```

Expected evidence:

- Target-aware generation writes Markdown context files and `.moonmind/vision/image_context_index.json` under a temporary workspace.
- The index records objective and step targets separately even when filenames match.

## Final Unit Suite

```bash
./tools/test_unit.sh
```

## Hermetic Integration Suite

```bash
./tools/test_integration.sh
```

Use the hermetic integration runner when Docker is available in the execution environment.
