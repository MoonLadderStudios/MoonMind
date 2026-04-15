# Tasks: Jira Orchestrate Preset

**Input**: Design documents from `/specs/173-jira-orchestrate-preset/`

## Phase 1: Preset Data

- [X] T001 Add global seeded `jira-orchestrate` preset in `api_service/data/task_step_templates/jira-orchestrate.yaml`
- [X] T002 Include required Jira issue key input and optional MoonSpec orchestration inputs
- [X] T003 Compose Jira In Progress, Jira preset brief loading, MoonSpec lifecycle, PR creation, Jira Code Review, and final report steps

## Phase 2: Tests

- [X] T004 Add catalog unit coverage for seed synchronization and expansion in `tests/unit/api/test_task_step_templates_service.py`
- [X] T005 Add startup seed coverage in `tests/integration/test_startup_task_template_seeding.py`

## Phase 3: Validation

- [X] T006 Run `pytest tests/unit/api/test_task_step_templates_service.py -q`
- [X] T007 Run `pytest tests/integration/test_startup_task_template_seeding.py -q`
- [X] T008 Run `./tools/test_unit.sh`
