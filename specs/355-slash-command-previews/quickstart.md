# Quickstart: Provider-Neutral Slash Command Previews

## Scope

Validate the single MM-685 story: Create page users see provider-neutral slash-command previews for objective and step instructions, with runtime changes, unknown commands, escaped literals, malformed/path-like input, and edit-mode restoration covered.

## Test-First Flow

1. Add failing frontend unit tests in `frontend/src/entrypoints/task-create.test.tsx`:
   - `/review` on a slash-capable runtime shows runtime command preview for objective and step instructions.
   - `/foo` on a slash-capable runtime shows pass-through status and no missing-hint warning.
   - Changing runtime to one without slash pass-through shows an actionable warning and preserves the exact textarea value.
   - `\/review` shows literal text intent and no executable command chip.
   - ` /review`, inline slash text, and `/src/app.ts is broken` do not show executable command preview.
   - Edit mode restores stored `runtimeCommand` metadata from the authoritative snapshot for preview.

2. Add failing Python/API-boundary tests if boot payload metadata is changed:
   - Dashboard boot payload includes browser-safe runtime command preview capabilities and known hints.
   - No secret or provider credential fields are included in preview metadata.

3. Implement the minimal code changes:
   - Add or expose a browser-safe runtime command preview catalog.
   - Render provider-neutral preview state in `TaskCreatePage` for objective and step instructions.
   - Preserve stored runtime command metadata through `buildTemporalSubmissionDraftFromExecution()` for preview restoration.
   - Keep submit-time backend normalization authoritative.

## Focused Commands

Prepare JS dependencies if needed through the standard test runner, then run focused frontend tests:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Run focused Python tests for backend/task-contract and boot payload changes:

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/routers/test_task_dashboard_view_model.py
```

Run hermetic integration coverage when API or task submission boundaries change:

```bash
./tools/test_integration.sh
```

Before final verification, run the required full unit suite:

```bash
./tools/test_unit.sh
```

## End-to-End Manual Check

1. Open Mission Control Create page.
2. Select a slash-capable runtime.
3. Enter `/review` in objective instructions and verify a runtime command preview appears.
4. Enter `/foo` and verify pass-through status appears without warning language.
5. Switch to a runtime without slash-command pass-through and verify the warning updates without changing the instruction text.
6. Enter `\/review` and verify literal text intent appears with no executable command chip.
7. Add a step with `/simplify` and verify step-level preview.
8. Open an editable task whose authoritative snapshot contains `runtimeCommand` metadata and verify the preview reflects stored metadata.

## Completion Evidence

- Unit tests cover the preview matrix from FR-010.
- Integration/API tests cover boot metadata or edit-boundary changes if touched.
- Final verification confirms `MM-685`, the original Jira preset brief, and all in-scope source design requirements remain preserved.
