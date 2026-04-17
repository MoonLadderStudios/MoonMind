# Quickstart: Protect Image Access and Untrusted Content Boundaries

## Source Traceability

```bash
rg -n "MM-374|DESIGN-REQ-016|DESIGN-REQ-017|DESIGN-REQ-020" specs/203-protect-image-access docs/tmp/jira-orchestration-inputs/MM-374-moonspec-orchestration-input.md
```

## Focused Unit Validation

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_artifact_authorization.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/agents/codex_worker/test_worker.py
```

## Focused UI Validation

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

## Focused Vision Context Validation

```bash
pytest tests/integration/vision/test_context_artifacts.py -q
```

## Full Required Validation

```bash
./tools/test_unit.sh
```

Run hermetic integration tests when Docker is available:

```bash
./tools/test_integration.sh
```

## Expected Evidence

- Artifact authorization denies non-owner access for restricted image-like artifacts.
- Task image UI links use MoonMind artifact endpoints and ignore external download URLs for target-aware image rendering.
- Worker materialization and runtime injection preserve exact artifact ids and target metadata.
- Vision context and runtime injection label image-derived text as untrusted and warn against executing instructions embedded in images.
- Task contract validation rejects embedded image data and data URLs.
