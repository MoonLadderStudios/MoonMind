# Quickstart: Task Presets Strategy Alignment

## 1. Confirm seed instruction contract

1. Open `api_service/data/task_step_templates/speckit-orchestrate.yaml`.
2. Inspect the final step instructions.
3. Verify the text explicitly states runtime agents must **not** commit/push/open PRs directly and must return a final report for publish-stage handling.
4. Verify mode placeholders are preserved in validation instructions, especially:
   - `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode {{ inputs.orchestration_mode }}`
   - `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode {{ inputs.orchestration_mode }} --base-ref origin/main`

## 2. Apply migration

1. Run Alembic upgrade to include `202603010001_align_speckit_orchestrate_publish_stage.py`.
2. Confirm migration completes without schema changes or errors.

## 3. Validate seeded data behavior

1. Query `task_step_templates` + `task_step_template_versions` for slug `speckit-orchestrate`.
2. Confirm `required_capabilities` and `steps` match the YAML seed document.

## 4. Run tests

1. Execute `./tools/test_unit.sh` from repository root.
2. Confirm `tests/unit/api/test_task_template_seed_alignment.py` passes and guards:
   - final-step publish-stage handoff language,
   - no direct commit/PR directives,
   - runtime-neutral required capabilities.

## 5. Spec/task completion

1. Ensure `specs/024-task-presets/tasks.md` marks all tasks `[X]`.
2. Verify file paths in spec artifacts correspond to current repository layout.

## 6. DOC-REQ traceability gate outcome

1. Confirm `specs/024-task-presets/spec.md` contains no concrete `DOC-REQ-*` requirement IDs.
2. Treat `contracts/requirements-traceability.md` as not required for this feature unless `DOC-REQ-*` IDs are introduced in a future revision.
