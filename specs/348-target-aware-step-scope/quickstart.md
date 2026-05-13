# Quickstart: Target-Aware Step Execution Scope

## Prerequisites

- Python 3.12 environment for MoonMind tests.
- Local repository dependencies installed by the standard test runner.
- No external provider credentials are required.

## Focused Unit Verification

Run prepared-context contract tests:

```bash
pytest tests/unit/workflows/tasks/test_prepared_context.py -q --tb=short
```

Run parent workflow request-scoping tests:

```bash
pytest tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py -q --tb=short
```

Expected evidence:
- Objective refs are included.
- Current-step refs are included.
- Sibling-step refs are absent.
- Invalid inline or missing attachment refs fail explicitly.
- Parent-owned prepared context metadata is preserved for AgentRun child inputs.

## Focused Integration Verification

Run the hermetic workflow boundary target-aware tests:

```bash
pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -m 'integration_ci' -q --tb=short
```

Expected evidence:
- The run boundary prepares objective plus current-step context only.
- Invalid step attachment preparation reports the affected target.
- Add or update integration coverage for AgentRun child input scoping if current evidence is insufficient for SC-002.

## Full Required Verification

Before closing implementation, run:

```bash
./tools/test_unit.sh
```

Then run hermetic integration tests:

```bash
./tools/test_integration.sh
```

## Story Validation

Validate a task with one objective attachment and at least two step-scoped attachments:

1. Select context for the first step.
2. Confirm the first step receives objective refs and first-step refs only.
3. Select context for the second step.
4. Confirm the second step receives objective refs and second-step refs only.
5. Dispatch or inspect the AgentRun child input for each represented step.
6. Confirm diagnostics identify parent-owned target binding and do not redefine target scope.

## Traceability

Final verification must preserve:

- Jira issue `MM-649`
- the canonical Jira preset brief in `spec.md`
- DESIGN-REQ-001 and DESIGN-REQ-002
- FR-001 through FR-009
- SC-001 through SC-005
