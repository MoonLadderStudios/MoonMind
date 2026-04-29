# Research: Governed Tool Step Authoring

## FR-001/FR-004 Manual Tool Draft Fields

Decision: Add editable Tool id, optional version, and JSON object inputs to the existing Create-page Tool panel.
Evidence: `frontend/src/entrypoints/task-create.tsx` already has `stepType: "tool"` and a Tool panel, but the Tool field is read-only and has an empty value.
Rationale: This is the smallest runtime slice that allows governed Tool authoring without inventing a separate catalog endpoint.
Alternatives considered: A full schema-driven picker was rejected for this slice because no Create-page tool catalog boot endpoint exists yet; leaving the field read-only fails MM-563.
Test implications: Frontend integration test for valid Tool submit.

## FR-003 Tool Input Validation

Decision: Validate Tool inputs as JSON object text before submission.
Evidence: Existing skill args validation already blocks invalid JSON object text before submit in `task-create.tsx`.
Rationale: Reusing the same client-side pattern gives immediate actionable feedback and preserves the downstream `tool.inputs` object contract.
Alternatives considered: Backend-only rejection was rejected because the acceptance criteria require actionable pre-submission validation.
Test implications: Frontend integration test for invalid JSON and no `/api/executions` call.

## FR-005 Shell/Script Guardrail

Decision: Extend `TaskStepSpec` forbidden step keys to include `command`, `cmd`, `script`, `shell`, and `bash`.
Evidence: `TaskTemplateCatalogService` already blocks those keys for saved presets, while `TaskExecutionSpec` step validation currently blocks task-scoped overrides but not shell/script keys.
Rationale: The execution contract boundary should reject shell-like step payloads even if they bypass Create-page UI.
Alternatives considered: UI-only blocking was rejected because direct API submissions still cross the task contract boundary.
Test implications: Python unit test for task contract rejection.

## DESIGN-REQ-015 Terminology

Decision: Keep Tool as the visible Step Type and continue omitting Script as an option.
Evidence: Existing frontend tests assert Step Type choices are Tool, Skill, and Preset.
Rationale: This matches the source design and avoids introducing a docs-only terminology change.
Alternatives considered: Renaming Tool to Executable was rejected by the source design.
Test implications: Existing frontend coverage remains, with Tool panel assertions added.
