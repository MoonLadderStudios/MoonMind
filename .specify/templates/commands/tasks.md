---
description: Generate a one-story, TDD-first Moon Spec tasks.md with unit tests, integration tests, and source-design traceability.
handoffs:
  - label: Analyze For Consistency
    agent: speckit.analyze
    prompt: Run a project analysis for consistency
    send: true
  - label: Implement Project
    agent: speckit.implement
    prompt: Start the implementation in phases
    send: true
  - label: Verify Final Implementation
    agent: speckit.verify
    prompt: Verify the completed implementation against the original feature request
scripts:
  sh: scripts/bash/check-prerequisites.sh --json
  ps: scripts/powershell/check-prerequisites.ps1 -Json
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before tasks generation)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.before_tasks` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
- For each executable hook, output the following based on its `optional` flag:
  - **Optional hook** (`optional: true`):
    ```
    ## Extension Hooks

    **Optional Pre-Hook**: {extension}
    Command: `/{command}`
    Description: {description}

    Prompt: {prompt}
    To execute: `/{command}`
    ```
  - **Mandatory hook** (`optional: false`):
    ```
    ## Extension Hooks

    **Automatic Pre-Hook**: {extension}
    Executing: `/{command}`
    EXECUTE_COMMAND: {command}
    
    Wait for the result of the hook command before proceeding to the Outline.
    ```
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

## Outline

Moon Spec tasks turn one declarative design-backed spec into an executable implementation checklist. The output MUST preserve single-story focus, make TDD the default path, include both unit and integration test work, and keep every task traceable to the original request or source design preserved in `spec.md`.

1. **Setup**: Run `{SCRIPT}` from repo root and parse FEATURE_DIR and AVAILABLE_DOCS list. All paths must be absolute. For single quotes in args like "I'm Groot", use escape syntax: e.g 'I'\''m Groot' (or double-quote if possible: "I'm Groot").

2. **Load design documents**: Read from FEATURE_DIR:
   - **Required**: plan.md (tech stack, libraries, structure), spec.md (single user story)
   - **Required**: `.specify/memory/constitution.md` for project constraints and test discipline
   - **Optional**: data-model.md (entities), contracts/ (interface contracts), research.md (decisions), quickstart.md (test scenarios), `specs/breakdown.md` (source design coverage context)
   - Note: Not all projects have all documents. Generate tasks based on what's available.

3. **Execute task generation workflow**:
   - Load plan.md and extract tech stack, libraries, project structure, unit testing tools, and integration testing tools
   - Load spec.md and extract the preserved `**Input**`, single user story, goal, independent test, acceptance scenarios, edge cases, functional requirements, success criteria, assumptions, and source design mappings such as `DESIGN-REQ-*` or `DOC-REQ-*`
   - Confirm spec.md contains exactly one independently testable user story; if it contains multiple stories, STOP and tell the user to use `/speckit.breakdown` for broad designs or regenerate a one-story spec with `/speckit.specify`
   - Build a traceability inventory covering each `FR-*`, acceptance scenario, edge case, measurable success criterion, and in-scope source design mapping
   - If data-model.md exists: Extract entities needed by the story
   - If contracts/ exists: Map interface contracts to the story and to integration/contract test tasks
   - If research.md exists: Extract decisions for setup tasks
   - If quickstart.md exists: Extract validation commands and end-to-end scenarios
   - Generate tasks organized around the single story (see Task Generation Rules below)
   - Generate phase dependencies showing setup, foundation, red-first tests, implementation, story validation, polish, and final verification order
   - Create parallel execution examples for the story phase
   - Validate task completeness: every traceability item has implementation coverage and either unit or integration test coverage, with integration coverage for acceptance scenarios and system boundaries

4. **Generate tasks.md**: Use `templates/tasks-template.md` as structure, fill with:
   - Correct feature name from plan.md
   - Input paths and source traceability summary, including the preserved original request/design source and any `DESIGN-REQ-*` or `DOC-REQ-*` mappings
   - Unit test command and integration test command from plan.md, quickstart.md, or project conventions
   - Phase 1: Setup tasks (project initialization, including unit test and integration test tooling from plan.md)
   - Phase 2: Foundational tasks (blocking prerequisites for the story, including integration test infrastructure such as Docker/Testcontainers/service fixtures when required)
   - Phase 3: The single story from spec.md
   - The story phase includes: story goal, independent test criteria, source traceability, required unit tests, required integration tests, red-first confirmation tasks, and implementation tasks
   - Final Phase: Polish & cross-cutting concerns
   - Final verification task that runs `/speckit.verify` after implementation and tests pass
   - All tasks must follow the strict checklist format (see Task Generation Rules below)
   - Clear file paths for each task, plus requirement/scenario/source IDs where applicable
   - Dependencies section showing phase and test-before-implementation order
   - Parallel execution examples for the story phase
   - Implementation strategy section (test-driven story delivery)

5. **Report**: Output path to generated tasks.md and summary:
   - Total task count
   - Task count per phase
   - Parallel opportunities identified
   - Independent test criteria for the story
   - Source design or original request coverage summary
   - Required unit and integration test coverage generated
   - Unit and integration test tooling captured separately when they differ
   - Final verification step included
   - Confirmation that generated tasks cover exactly one story and include no P1/P2/P3 or multi-story phases
   - Format validation: Confirm ALL tasks follow the checklist format (checkbox, ID, parallel marker where applicable, file paths)

6. **Check for extension hooks**: After tasks.md is generated, check if `.specify/extensions.yml` exists in the project root.
   - If it exists, read it and look for entries under the `hooks.after_tasks` key
   - If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
   - Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
   - For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
     - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
     - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
   - For each executable hook, output the following based on its `optional` flag:
     - **Optional hook** (`optional: true`):
       ```
       ## Extension Hooks

       **Optional Hook**: {extension}
       Command: `/{command}`
       Description: {description}

       Prompt: {prompt}
       To execute: `/{command}`
       ```
     - **Mandatory hook** (`optional: false`):
       ```
       ## Extension Hooks

       **Automatic Hook**: {extension}
       Executing: `/{command}`
       EXECUTE_COMMAND: {command}
       ```
   - If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

Context for task generation: {ARGS}

The tasks.md should be immediately executable - each task must be specific enough that an LLM can complete it without additional context.

## Task Generation Rules

**CRITICAL**: Tasks MUST be organized around the single user story from spec.md so implementation remains focused, traceable to the original request or declarative design, and independently testable.

**Tests are REQUIRED**: Generate unit test tasks and integration test tasks before implementation tasks. The implementation plan MUST follow a test-first workflow where tests are written and confirmed failing for the intended reason before production code is implemented.

**Source traceability is REQUIRED**: Every `FR-*`, acceptance scenario, edge case, measurable success criterion, and in-scope `DESIGN-REQ-*` or `DOC-REQ-*` must be covered by at least one task. Do not rely on copied requirement text as evidence; tasks must point to concrete files, tests, or validation commands.

### Checklist Format (REQUIRED)

Every task MUST strictly follow this format:

```text
- [ ] [TaskID] [P?] Description with file path
```

**Format Components**:

1. **Checkbox**: ALWAYS start with `- [ ]` (markdown checkbox)
2. **Task ID**: Sequential number (T001, T002, T003...) in execution order
3. **[P] marker**: Include ONLY if task is parallelizable (different files, no dependencies on incomplete tasks)
4. **Description**: Clear action with exact file path and requirement/scenario/source ID where applicable

**Examples**:

- ✅ CORRECT: `- [ ] T001 Create project structure in src/ and tests/ per implementation plan`
- ✅ CORRECT: `- [ ] T005 [P] Implement authentication middleware in src/middleware/auth.py`
- ✅ CORRECT: `- [ ] T012 [P] Add failing unit test for User validation FR-001 in tests/unit/test_user.py`
- ✅ CORRECT: `- [ ] T013 [P] Add failing integration test for signup scenario SCN-001 in tests/integration/test_signup.py`
- ✅ CORRECT: `- [ ] T014 Implement UserService for FR-001 in src/services/user_service.py`
- ❌ WRONG: `- [ ] Create User model` (missing ID)
- ❌ WRONG: `T001 Create model` (missing checkbox)
- ❌ WRONG: `- [ ] [US1] Create User model` (unneeded story label; this task list covers one story)
- ❌ WRONG: `- [ ] T001 Create model` (missing file path)
- ❌ WRONG: `- [ ] T010 Write tests later if needed` (tests are required and must run before implementation)

### Task Organization

1. **From Single User Story (spec.md)** - PRIMARY ORGANIZATION:
   - Create exactly one story phase
   - Map all related components to the story:
     - Unit tests for domain behavior and edge cases using the unit testing tools from plan.md
     - Integration tests for acceptance scenarios and end-to-end behavior using the integration testing tools from plan.md
     - Red-first validation tasks that run the new tests before production implementation
     - Models needed for the story
     - Services needed for the story
     - Interfaces/UI needed for the story
     - Final story validation commands
   - The story must remain independently testable
   - Do not create P1/P2/P3 phases, multiple story phases, or tasks for future stories

2. **From Contracts**:
   - Map each interface contract → to the story it serves
   - Each relevant interface contract → integration or contract test task [P] before implementation in the story phase
   - Contract implementation tasks must reference both the contract file and the scenario or requirement they satisfy

3. **From Data Model**:
   - Map each entity to the story if needed
   - If entity is shared infrastructure rather than story-specific behavior: Put it in Foundational phase
   - Relationships → service layer tasks in appropriate story phase

4. **From Setup/Infrastructure**:
   - Shared infrastructure → Setup phase (Phase 1)
   - Unit test tooling/configuration → Setup phase (Phase 1)
   - Integration test tooling/configuration → Setup phase (Phase 1)
   - Integration-only runtime dependencies (Docker, service containers, external emulators, seed data, test fixtures) → Foundational phase (Phase 2)
   - Foundational/blocking tasks → Foundational phase (Phase 2)
   - Story-specific setup → within the story phase

5. **From Source Design Coverage**:
   - Each in-scope `DESIGN-REQ-*` or `DOC-REQ-*` → at least one implementation task
   - Each source design workflow or public boundary → at least one integration test task
   - Each source design rule, invariant, or edge case → at least one unit test task where applicable
   - Explicit non-goals and constraints → guardrail tests, validation tasks, or documented scope checks when they affect implementation

### Phase Structure

- **Phase 1**: Setup (project initialization)
- **Phase 2**: Foundational (blocking prerequisites - MUST complete before story work)
- **Phase 3**: Story
  - Within the story: Unit Tests → Integration Tests → Red-first confirmation → Models → Services → Endpoints/UI/CLI → Integration wiring → Story validation
  - Unit and integration tests MUST be written and confirmed failing before implementation
  - The phase should be a complete, independently testable vertical slice
- **Final Phase**: Polish & Cross-Cutting Concerns
  - Refactoring, documentation, performance, security, and additional coverage that strengthen the completed story without adding hidden scope
  - Include a final task to run `/speckit.verify` after all implementation and tests pass
