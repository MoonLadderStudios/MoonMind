# Tasks: Seeded MoonSpec Preset Availability

**Input**: Design documents from `specs/093-speckit-preset-seeding/`
**Prerequisites**: spec.md, plan.md

## Phase 1: Implementation

- [X] T001 Add seed-template synchronization support to `api_service/services/task_templates/catalog.py` so YAML seeds can create or refresh existing catalog rows.
- [X] T002 Invoke seeded task-template synchronization from `api_service/main.py` during startup behind the existing task preset catalog feature flag.
- [X] T003 Ensure startup seed sync fails soft when preset tables are unavailable instead of aborting startup.

## Phase 2: Verification

- [X] T004 Add unit coverage in `tests/unit/api/test_task_step_templates_service.py` for missing-seed creation and existing-seed refresh.
- [X] T005 Add startup integration coverage in `tests/integration/test_startup_task_template_seeding.py` verifying `moonspec-orchestrate` exists after startup.
- [X] T006 Run `./tools/test_unit.sh` and confirm the targeted preset seeding regressions pass.
- [X] T007 Update the seeded preset payload to use `moonspec-orchestrate`, `moonspec-*` skill calls, and `moonspec-align` instead of analyze remediation prompt loops.
