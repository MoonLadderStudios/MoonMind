---
description: Verify the final implementation against the original feature request, specification, plan, tasks, and required tests.
scripts:
  sh: scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
  ps: scripts/powershell/check-prerequisites.ps1 -Json -RequireTasks -IncludeTasks
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before final verification)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.before_verify` key
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

This command is the final Moon Spec check. It verifies that the completed implementation satisfies the original feature request preserved in `spec.md`, not just the later task list.

1. **Setup**: Run `{SCRIPT}` from repo root and parse FEATURE_DIR and AVAILABLE_DOCS list. All paths must be absolute. For single quotes in args like "I'm Groot", use escape syntax: e.g 'I'\''m Groot' (or double-quote if possible: "I'm Groot").

2. **Load verification sources**:
   - **Required**: `spec.md`, `plan.md`, `tasks.md`
   - **If exists**: `research.md`, `data-model.md`, `contracts/`, `quickstart.md`, `checklists/`
   - Extract the original request from the `**Input**` field in `spec.md`
   - Extract the single user story, acceptance scenarios, functional requirements, edge cases, success criteria, assumptions, and independent test

3. **Inspect implementation evidence**:
   - Review changed and relevant source files named by `tasks.md`, `plan.md`, contracts, and quickstart
   - Confirm all tasks in `tasks.md` are marked complete
   - Confirm production code exists for each functional requirement
   - Confirm unit tests exist for domain behavior and edge cases
   - Confirm integration tests exist for acceptance scenarios, external interfaces, persistence, workflows, or other system interactions
   - Confirm implementation does not add hidden scope that contradicts the original request

4. **Run verification commands when available**:
   - Run unit test commands from `plan.md`, `tasks.md`, or project conventions
   - Run integration test commands from `plan.md`, `tasks.md`, quickstart, or project conventions
   - Run quickstart validation when `quickstart.md` exists and can be executed safely
   - Do not modify files during verification except for normal test artifacts already ignored by the project
   - If a command is unsafe, unavailable, or requires missing credentials/services, record it as "Not run" with the exact reason

5. **Compare implementation to the original request**:
   - For each requirement and acceptance scenario, classify evidence as Pass, Partial, Fail, or Not Verified
   - Check whether success criteria are directly validated, indirectly supported, or unverified
   - Check whether assumptions made during specification still hold
   - Check whether integration tests cover the end-to-end behavior implied by the original request
   - Treat missing required unit or integration tests as a verification failure unless the spec explicitly makes that class irrelevant

6. **Produce the final verification report**:

   ```markdown
   # Final Verification Report

   **Feature**: [name]
   **Spec**: [path]
   **Original Request Source**: spec.md `Input`
   **Overall Status**: PASS | PASS WITH RISKS | FAIL

   ## Test Results

   | Suite | Command | Result | Notes |
   |-------|---------|--------|-------|
   | Unit | [command] | PASS/FAIL/NOT RUN | [notes] |
   | Integration | [command] | PASS/FAIL/NOT RUN | [notes] |

   ## Requirement Coverage

   | Requirement | Evidence | Status | Notes |
   |-------------|----------|--------|-------|
   | FR-001 | [file/test/reference] | Pass/Partial/Fail/Not Verified | [notes] |

   ## Acceptance Scenario Coverage

   | Scenario | Evidence | Status | Notes |
   |----------|----------|--------|-------|

   ## Original Request Alignment

   - [Pass/fail summary against the verbatim original request]

   ## Gaps

   - [Blocking gaps first]

   ## Decision

   - PASS only if implementation, unit tests, integration tests, and original request alignment all pass.
   ```

7. **Report completion**:
   - If PASS: state that final verification passed and list the test commands run
   - If PASS WITH RISKS: list non-blocking risks and unverified areas
   - If FAIL: list blocking gaps and the files or tests that need follow-up

## Post-Execution Checks

**Check for extension hooks (after final verification)**:
Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.after_verify` key
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

## Key Rules

- Verification is read-only except for normal test artifacts.
- The original request in `spec.md` is the source of truth for final alignment.
- Unit tests and integration tests are both expected evidence.
- Do not mark PASS when required behavior is only inferred and not verified.
