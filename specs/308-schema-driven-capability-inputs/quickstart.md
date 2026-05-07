# Quickstart: Schema-Driven Capability Inputs

## Goal

Validate the single MM-593 selected story before implementation starts and after implementation completes: capability input schemas render in the Create page, Jira issue input uses reusable metadata-driven UI, validation is field-addressable, and Jira credentials remain isolated.

## Test-First Workflow

1. Add failing backend unit tests for normalized capability input contract loading and validation.
   - Suggested target: `tests/unit/api/test_task_step_templates_service.py`
   - Cover `inputSchema`, `uiSchema`, defaults, duplicate/invalid fields, and secret-like defaults.

2. Add failing frontend unit/integration tests for schema-generated Create-page fields.
   - Suggested target: `frontend/src/entrypoints/task-create.test.tsx`
   - Cover preset schema rendering, skill schema rendering, required field errors, unsupported widget behavior, and no capability-ID-specific rendering.

3. Add failing Jira issue picker tests.
   - Suggested target: `frontend/src/entrypoints/task-create.test.tsx`
   - Cover `uiSchema` widget selection, `x-moonmind-widget` selection, manual key preservation, unavailable lookup behavior, and safe value submission.

4. Add backend validation/enrichment tests where trusted Jira tooling is involved.
   - Suggested targets: `tests/unit/integrations/test_jira_tool_service.py`, `tests/unit/mcp/test_jira_tool_registry.py`, or task-template service tests depending on implementation boundary.

## Focused Commands

Frontend-focused iteration after dependencies are prepared:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Python-focused iteration for task-template/Jira validation boundaries:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only -- tests/unit/api/test_task_step_templates_service.py tests/unit/integrations/test_jira_tool_service.py tests/unit/mcp/test_jira_tool_registry.py
```

Full required unit verification before finishing implementation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Hermetic integration verification if task-template API/seed behavior changes require compose-backed proof:

```bash
./tools/test_integration.sh
```

Final MoonSpec verification after implementation and required tests pass:

```bash
/moonspec-verify
```

## End-To-End Acceptance Checks

- Select a seeded preset with `inputSchema` and `uiSchema`; generated fields appear without a preset-specific form branch.
- Select a skill with the same input shape; generated fields render through the same path.
- Configure a Jira issue field through metadata-selected `jira.issue-picker`; the safe value contains at least `{ "key": "MM-593" }`.
- Submit/preview/apply-dependent validation blocks missing required values with field-addressable errors while preserving draft values.
- Simulate Jira lookup unavailable with manual entry allowed; manually entered issue key remains in the draft.
- Add a fixture capability with supported schema fields and existing widgets; it renders without code that checks the fixture capability ID.
- Confirm raw Jira credentials or secret-like defaults do not appear in schema defaults, draft safe values, submitted payloads, logs, artifacts, or agent-visible content in covered flows.

## Implementation Verification Notes

- `npm run ui:test:task-create`: passed for the Create-page schema-driven capability input coverage.
- `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`: passed. `npm run ui:typecheck` is blocked in this managed shell because the npm script did not resolve `tsc` from `node_modules/.bin`.
- `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only -- tests/unit/api/test_task_step_templates_service.py tests/unit/integrations/test_jira_tool_service.py tests/unit/mcp/test_jira_tool_registry.py tests/integration/test_startup_task_template_seeding.py`: passed.
- `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`: blocked before the UI target by unrelated active skill projection failures. The managed `.agents/skills` projection is missing `fix-comments` and `pr-resolver` tool files required by existing PR resolver tests.
