---
description: Generate an actionable, dependency-ordered tasks.md for the feature based on available design artifacts.
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

1. **Setup**: Run `{SCRIPT}` from repo root and parse FEATURE_DIR and AVAILABLE_DOCS list. All paths must be absolute. For single quotes in args like "I'm Groot", use escape syntax: e.g 'I'\''m Groot' (or double-quote if possible: "I'm Groot").

2. **Load design documents**: Read from FEATURE_DIR:
   - **Required**: plan.md (tech stack, libraries, structure), spec.md (single user story)
   - **Optional**: data-model.md (entities), contracts/ (interface contracts), research.md (decisions), quickstart.md (test scenarios)
   - Note: Not all projects have all documents. Generate tasks based on what's available.

3. **Execute task generation workflow**:
   - Load plan.md and extract tech stack, libraries, project structure, unit testing tools, and integration testing tools
   - Load spec.md and extract the single user story, goal, independent test, acceptance scenarios, edge cases, and functional requirements
   - If data-model.md exists: Extract entities needed by the story
   - If contracts/ exists: Map interface contracts to the story
   - If research.md exists: Extract decisions for setup tasks
   - Generate tasks organized around the single story (see Task Generation Rules below)
   - Generate phase dependencies showing setup, foundation, test-first story work, and polish order
   - Create parallel execution examples for the story phase
   - Validate task completeness (the story has all needed tasks, required tests, and is independently testable)

4. **Generate tasks.md**: Use `templates/tasks-template.md` as structure, fill with:
   - Correct feature name from plan.md
   - Phase 1: Setup tasks (project initialization, including unit test and integration test tooling from plan.md)
   - Phase 2: Foundational tasks (blocking prerequisites for the story, including integration test infrastructure such as Docker/Testcontainers/service fixtures when required)
   - Phase 3: The single story from spec.md
   - The story phase includes: story goal, independent test criteria, required unit tests, required integration tests, implementation tasks
   - Final Phase: Polish & cross-cutting concerns
   - Final verification task that runs `/speckit.verify` after implementation and tests pass
   - All tasks must follow the strict checklist format (see Task Generation Rules below)
   - Clear file paths for each task
   - Dependencies section showing phase and test-before-implementation order
   - Parallel execution examples for the story phase
   - Implementation strategy section (test-driven story delivery)

5. **Report**: Output path to generated tasks.md and summary:
   - Total task count
   - Task count per phase
   - Parallel opportunities identified
   - Independent test criteria for the story
   - Required unit and integration test coverage generated
   - Unit and integration test tooling captured separately when they differ
   - Final verification step included
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

**CRITICAL**: Tasks MUST be organized around the single user story from spec.md so implementation remains focused, traceable, and independently testable.

**Tests are REQUIRED**: Generate unit test tasks and integration test tasks before implementation tasks. The implementation plan MUST follow a test-first workflow where tests are written and confirmed failing for the intended reason before production code is implemented.

### Checklist Format (REQUIRED)

Every task MUST strictly follow this format:

```text
- [ ] [TaskID] [P?] Description with file path
```

**Format Components**:

1. **Checkbox**: ALWAYS start with `- [ ]` (markdown checkbox)
2. **Task ID**: Sequential number (T001, T002, T003...) in execution order
3. **[P] marker**: Include ONLY if task is parallelizable (different files, no dependencies on incomplete tasks)
4. **Description**: Clear action with exact file path

**Examples**:

- ✅ CORRECT: `- [ ] T001 Create project structure per implementation plan`
- ✅ CORRECT: `- [ ] T005 [P] Implement authentication middleware in src/middleware/auth.py`
- ✅ CORRECT: `- [ ] T012 [P] Add failing unit test for User validation in tests/unit/test_user.py`
- ✅ CORRECT: `- [ ] T014 Implement UserService in src/services/user_service.py`
- ❌ WRONG: `- [ ] Create User model` (missing ID)
- ❌ WRONG: `T001 Create model` (missing checkbox)
- ❌ WRONG: `- [ ] [US1] Create User model` (unneeded story label; this task list covers one story)
- ❌ WRONG: `- [ ] T001 Create model` (missing file path)

### Task Organization

1. **From Single User Story (spec.md)** - PRIMARY ORGANIZATION:
   - Create exactly one story phase
   - Map all related components to the story:
     - Unit tests for domain behavior and edge cases using the unit testing tools from plan.md
     - Integration tests for acceptance scenarios and end-to-end behavior using the integration testing tools from plan.md
     - Models needed for the story
     - Services needed for the story
     - Interfaces/UI needed for the story
   - The story must remain independently testable

2. **From Contracts**:
   - Map each interface contract → to the story it serves
   - Each relevant interface contract → integration or contract test task [P] before implementation in the story phase

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

### Phase Structure

- **Phase 1**: Setup (project initialization)
- **Phase 2**: Foundational (blocking prerequisites - MUST complete before story work)
- **Phase 3**: Story
  - Within the story: Unit Tests → Integration Tests → Models → Services → Endpoints → Integration
  - Unit and integration tests MUST be written and confirmed failing before implementation
  - The phase should be a complete, independently testable vertical slice
- **Final Phase**: Polish & Cross-Cutting Concerns
  - Include a final task to run `/speckit.verify` after all implementation and tests pass
