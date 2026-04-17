# Quickstart: Expose Image Diagnostics and Failure Evidence

## Focused Unit Validation

Run focused tests while implementing:

```bash
./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py tests/unit/moonmind/vision/test_service.py tests/unit/api/routers/test_temporal_artifacts.py tests/unit/workflows/tasks/test_task_contract.py
```

Expected coverage:
- attachment upload started/completed diagnostics include authoritative target metadata
- attachment validation failed diagnostics include target metadata and failure details
- prepare download started/completed/failed diagnostics include authoritative target metadata
- task context diagnostics expose attachment manifest and context evidence paths
- image context generation diagnostics cover started/completed/failed/disabled statuses
- step-scoped failures identify the affected step target

## Integration Validation

When Docker is available, run the hermetic integration suite:

```bash
./tools/test_integration.sh
```

For focused filesystem artifact validation:

```bash
./tools/test_unit.sh tests/integration/vision/test_context_artifacts.py
```

## Final Unit Validation

Before completion, run:

```bash
./tools/test_unit.sh
```

## Traceability Check

Verify MM-375 and DESIGN-REQ-019 are preserved:

```bash
rg -n "MM-375|DESIGN-REQ-019" specs/203-expose-image-diagnostics docs/tmp/jira-orchestration-inputs/MM-375-moonspec-orchestration-input.md
```
